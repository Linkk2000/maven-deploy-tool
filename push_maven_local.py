from __future__ import annotations

import sys

from maven_push_tool.config import load_config, resolve_runtime_context, validate_config
from maven_push_tool.deployer import deploy_record
from maven_push_tool.models import (
    DEPLOY_DRY_RUN,
    DEPLOY_FAILED_PRECHECK,
    DEPLOY_SKIPPED_EXISTING,
    DEPLOY_SUCCESS,
    PRECHECK_EXISTS,
    PRECHECK_FAILED,
    PRECHECK_NOT_FOUND,
    PRECHECK_SKIPPED,
    ReportSummary,
    VALIDATION_INVALID,
)
from maven_push_tool.parser import build_record_from_dir, validate_record
from maven_push_tool.precheck import precheck_remote
from maven_push_tool.reporter import Reporter
from maven_push_tool.resolver import resolve_target_repo
from maven_push_tool.scanner import build_scan_plan, scan_version_dirs
from maven_push_tool.selector import apply_selection_rules


def main(argv: list[str] | None = None) -> int:
    config = load_config(argv)
    reporter = Reporter(config)
    runtime = None
    summary = ReportSummary(dry_run=config.dry_run)

    try:
        validate_config(config)
        runtime = resolve_runtime_context(config)
        reporter.info(f"START    dryRun={config.dry_run} retry={config.retry} timeout={config.timeout}s")
        reporter.info(f"REPO     local={runtime.local_repo}")
        reporter.info("SETTINGS effective=%s", runtime.effective_settings_file or "<default-not-found>")
        reporter.info("MAVEN    binary=%s", runtime.effective_mvn_bin)
        reporter.info(
            "TARGET   release=%s snapshot=%s",
            config.release_repo_url,
            config.snapshot_repo_url,
        )

        if config.threads != 1:
            reporter.warning("V1 当前仍按串行执行，--threads 已记录但暂未启用并发。")

        scan_plan = build_scan_plan(runtime.local_repo, config)
        if scan_plan.roots:
            reporter.info("SCANROOT mode=%s roots=%s", scan_plan.mode, len(scan_plan.roots))
            for root in scan_plan.roots:
                reporter.info("SCANROOT path=%s", root)
        else:
            reporter.warning("扫描根目录为空，未找到与当前筛选条件对应的本地目录。")

        candidate_dirs = scan_version_dirs(scan_plan.roots)
        summary.scan_total = len(candidate_dirs)
        reporter.info(f"SCAN     检测到候选版本目录 {summary.scan_total} 个")

        records = [build_record_from_dir(path, runtime.local_repo) for path in candidate_dirs]
        selected_records = apply_selection_rules(records, config)
        summary.filtered_total = len(selected_records)
        reporter.info(f"FILTER   筛选后命中构件 {summary.filtered_total} 个")
        if summary.filtered_total == 0:
            reporter.warning("未匹配到任何构件，请检查 --gav 与 POM 实际坐标是否一致。")

        for record in selected_records:
            validate_record(record, config)
            if record.validation_status == VALIDATION_INVALID:
                summary.validation_failed += 1
                reporter.record_failure(record)
                if should_stop(config):
                    break
                continue

            resolve_target_repo(record, config)
            if record.repo_type == "release":
                summary.release_total += 1
            else:
                summary.snapshot_total += 1

            reporter.event(
                "VALIDATE",
                record,
                "CHECK",
                "OK",
                f"selectedBy={record.selected_by} repoId={record.target_repo_id}",
            )
            for warning in record.warnings:
                reporter.warning(f"{record.gav()} {warning}")

            if record.repo_type == "release" and config.release_precheck:
                status = precheck_remote(record, config, runtime.settings_info)
                if status == PRECHECK_EXISTS:
                    summary.precheck_exists += 1
                    reporter.event("PRECHECK", record, "CHECK", PRECHECK_EXISTS)
                    if config.skip_existing or not config.allow_redeploy:
                        record.deploy_status = DEPLOY_SKIPPED_EXISTING
                        summary.deploy_skipped += 1
                        reporter.event("DEPLOY", record, "SKIP", DEPLOY_SKIPPED_EXISTING)
                        continue
                elif status == PRECHECK_NOT_FOUND:
                    summary.precheck_not_found += 1
                    reporter.event("PRECHECK", record, "CHECK", PRECHECK_NOT_FOUND)
                elif status == PRECHECK_FAILED:
                    summary.precheck_failed += 1
                    if config.fail_on_precheck_error:
                        reporter.record_failure(record)
                        record.deploy_status = DEPLOY_FAILED_PRECHECK
                        summary.deploy_failed += 1
                        if should_stop(config):
                            break
                        continue
                    reporter.warning(f"{record.gav()} 预检失败但按配置继续执行: {record.error_message}")
            else:
                record.precheck_status = PRECHECK_SKIPPED

            if config.dry_run:
                record.deploy_status = DEPLOY_DRY_RUN
                reporter.event(
                    "DEPLOY",
                    record,
                    "DRY-RUN",
                    DEPLOY_DRY_RUN,
                    f"url={record.target_repo_url}",
                )
                continue

            deploy_record(record, config, runtime)
            if record.deploy_status == DEPLOY_SUCCESS:
                summary.deploy_success += 1
                reporter.event("DEPLOY", record, "EXECUTE", DEPLOY_SUCCESS)
                reporter.record_success(record)
                continue

            summary.deploy_failed += 1
            reporter.record_failure(record)
            if should_stop(config):
                break

        reporter.write_failed_files()
        reporter.write_report(summary)
        reporter.log_summary(summary)
        return 0 if not reporter.failures else 1
    except Exception as exc:
        reporter.error(str(exc))
        reporter.write_failed_files()
        reporter.write_report(summary)
        reporter.log_summary(summary)
        return 1
    finally:
        if runtime is not None:
            for temp_file in runtime.temp_files:
                try:
                    temp_file.unlink(missing_ok=True)
                except OSError:
                    pass


def should_stop(config) -> bool:
    return config.stop_on_first_error or not config.continue_on_error


if __name__ == "__main__":
    sys.exit(main())
