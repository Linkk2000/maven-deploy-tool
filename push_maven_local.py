from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from maven_push_tool.config import load_config, resolve_runtime_context, validate_config
from maven_push_tool.deployer import deploy_records_parallel
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
        reporter.info(f"START    dryRun={config.dry_run} retry={config.retry} timeout={config.timeout}s threads={config.threads}")
        reporter.info(f"REPO     local={runtime.local_repo}")
        reporter.info("SETTINGS effective=%s", runtime.effective_settings_file or "<default-not-found>")
        reporter.info("MAVEN    binary=%s", runtime.effective_mvn_bin)
        reporter.info(
            "TARGET   release=%s snapshot=%s",
            config.release_repo_url,
            config.snapshot_repo_url,
        )
        reporter.info("FILTERCFG packaging=%s", ",".join(sorted(config.packaging)))
        reporter.info(
            "SNAPCFG  historyMode=%s historyCount=%s buildMode=%s",
            config.snapshot_history_mode,
            config.snapshot_history_count,
            config.snapshot_build_mode,
        )

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

        records = [build_record_from_dir(path, runtime.local_repo, config) for path in candidate_dirs]
        selected_records = apply_selection_rules(records, config)
        summary.filtered_total = len(selected_records)
        reporter.info(f"FILTER   筛选后命中构件 {summary.filtered_total} 个")
        if summary.filtered_total == 0:
            reporter.warning("未匹配到任何构件，请检查 --gav 与 POM 实际坐标是否一致。")

        # ---- 阶段1：校验 + 解析目标仓库（纯CPU，速度快，串行即可） ----
        deployable_records: list[ArtifactRecord] = []
        for idx, record in enumerate(selected_records, 1):
            reporter.info("PROCESS  [%d/%d] dir=%s", idx, summary.filtered_total, record.version_dir)
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
                build_validate_detail(record),
            )
            for warning in record.warnings:
                reporter.warning(f"{record.gav()} {warning}")

            deployable_records.append(record)

        # ---- 阶段2：并行预检 release 构件 ----
        precheck_records = [
            r for r in deployable_records
            if r.repo_type == "release" and config.release_precheck
        ]
        if precheck_records:
            reporter.info("PRECHECK 并行预检 %d 个 release 构件...", len(precheck_records))
            _start = time.monotonic()

            precheck_results: dict[int, str] = {}
            precheck_lock = Lock()
            precheck_done = [0]

            def _do_precheck(rec: ArtifactRecord) -> str:
                status = precheck_remote(rec, config, runtime.settings_info)
                with precheck_lock:
                    precheck_done[0] += 1
                    precheck_results[id(rec)] = status
                return status

            max_precheck_workers = min(len(precheck_records), max(config.threads * 2, 8))
            with ThreadPoolExecutor(max_workers=max_precheck_workers) as executor:
                futures = {executor.submit(_do_precheck, r): r for r in precheck_records}
                for future in futures:
                    future.result()

            for rec in precheck_records:
                status = precheck_results.get(id(rec), PRECHECK_FAILED)
                if status == PRECHECK_EXISTS:
                    summary.precheck_exists += 1
                    reporter.event("PRECHECK", rec, "CHECK", PRECHECK_EXISTS)
                    if config.skip_existing or not config.allow_redeploy:
                        rec.deploy_status = DEPLOY_SKIPPED_EXISTING
                        summary.deploy_skipped += 1
                        reporter.event("DEPLOY", rec, "SKIP", DEPLOY_SKIPPED_EXISTING)
                elif status == PRECHECK_NOT_FOUND:
                    summary.precheck_not_found += 1
                    reporter.event("PRECHECK", rec, "CHECK", PRECHECK_NOT_FOUND)
                elif status == PRECHECK_FAILED:
                    summary.precheck_failed += 1
                    if config.fail_on_precheck_error:
                        reporter.record_failure(rec)
                        rec.deploy_status = DEPLOY_FAILED_PRECHECK
                        summary.deploy_failed += 1
                    else:
                        reporter.warning(f"{rec.gav()} 预检失败但按配置继续执行: {rec.error_message}")

            _elapsed = time.monotonic() - _start
            reporter.info("PRECHECK 完成，耗时 %.1fs", _elapsed)
        else:
            for rec in deployable_records:
                if rec.repo_type == "release" and config.release_precheck:
                    pass
                else:
                    rec.precheck_status = PRECHECK_SKIPPED

        # ---- 过滤出需要实际部署的构件 ----
        to_deploy: list[ArtifactRecord] = []
        for rec in deployable_records:
            if rec.deploy_status in (DEPLOY_SKIPPED_EXISTING, DEPLOY_FAILED_PRECHECK):
                continue
            if config.dry_run:
                rec.deploy_status = DEPLOY_DRY_RUN
                reporter.event(
                    "DEPLOY",
                    rec,
                    "DRY-RUN",
                    DEPLOY_DRY_RUN,
                    f"url={rec.target_repo_url}",
                )
                continue
            to_deploy.append(rec)

        # ---- 阶段3：并行部署 ----
        if to_deploy:
            effective_threads = max(config.threads, 1)
            reporter.info(
                "DEPLOY   开始部署 %d 个构件，并发线程=%d",
                len(to_deploy),
                effective_threads,
            )
            _start = time.monotonic()

            deploy_lock = Lock()
            deploy_done = [0]

            def on_deploy_complete(rec: ArtifactRecord) -> None:
                with deploy_lock:
                    deploy_done[0] += 1
                    current = deploy_done[0]

                if rec.deploy_status == DEPLOY_SUCCESS:
                    reporter.event("DEPLOY", rec, "EXECUTE", DEPLOY_SUCCESS)
                    reporter.record_success(rec)
                else:
                    reporter.record_failure(rec)

                reporter.info("PROGRESS %d/%d 完成", current, len(to_deploy))

            deploy_records_parallel(
                to_deploy,
                config,
                runtime,
                max_workers=effective_threads,
                on_complete=on_deploy_complete,
            )

            for rec in to_deploy:
                if rec.deploy_status == DEPLOY_SUCCESS:
                    summary.deploy_success += 1
                elif rec.deploy_status not in (DEPLOY_SKIPPED_EXISTING, DEPLOY_DRY_RUN):
                    summary.deploy_failed += 1
                    if should_stop(config):
                        break

            _elapsed = time.monotonic() - _start
            reporter.info("DEPLOY   完成，耗时 %.1fs", _elapsed)

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


def build_validate_detail(record) -> str:
    details = [f"selectedBy={record.selected_by}", f"repoId={record.target_repo_id}"]
    if record.snapshot_timestamp and record.snapshot_build_number:
        details.append(f"snapshotBuild={record.snapshot_timestamp}-{record.snapshot_build_number}")
    if record.parent_pom_source:
        details.append(f"parentSource={record.parent_pom_source}")
    return " ".join(details)


if __name__ == "__main__":
    sys.exit(main())
