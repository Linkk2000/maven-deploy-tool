from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from .config import AppConfig
from .models import ArtifactRecord, ReportSummary


class Reporter:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.logger = logging.getLogger("maven-push-tool")
        self.logger.setLevel(getattr(logging, config.log_level, logging.INFO))
        self.logger.handlers.clear()
        self.logger.propagate = False
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

        console = logging.StreamHandler()
        console.setFormatter(formatter)
        self.logger.addHandler(console)

        if config.log_file:
            ensure_parent(config.log_file)
            mode = "a" if config.append_log else "w"
            file_handler = logging.FileHandler(config.log_file, mode=mode, encoding="utf-8")
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)

        self.failures: list[ArtifactRecord] = []

    def info(self, message: str, *args: object) -> None:
        self.logger.info(message, *args)

    def event(self, stage: str, record: ArtifactRecord, action: str, result: str, detail: str = "") -> None:
        repo_type = record.repo_type or "-"
        packaging = record.packaging or "-"
        message = f"{stage:<8} {record.gav()} {packaging} {repo_type} {action} {result}"
        if detail:
            message = f"{message} {detail}"
        self.logger.info(message)

    def warning(self, message: str) -> None:
        self.logger.warning(message)

    def error(self, message: str) -> None:
        self.logger.error(message)

    def record_failure(self, record: ArtifactRecord) -> None:
        self.failures.append(record)
        self.logger.error(
            "FAILED   %s %s %s %s",
            record.gav(),
            record.packaging or "-",
            record.error_stage or "-",
            record.error_message or "",
        )
        if record.stdout_snippet:
            self.logger.error("MAVEN-STDOUT %s", record.stdout_snippet)
        if record.stderr_snippet:
            self.logger.error("MAVEN-STDERR %s", record.stderr_snippet)

    def record_success(self, record: ArtifactRecord) -> None:
        self.logger.info(
            "上传成功 %s packaging=%s repoType=%s repoId=%s",
            record.gav(),
            record.packaging or "-",
            record.repo_type or "-",
            record.target_repo_id or "-",
        )
        if record.target_repo_url:
            self.logger.info("SUCCESS  目标仓库 %s", record.target_repo_url)
        deploy_file = record.file_for_deploy()
        if deploy_file:
            self.logger.info("SUCCESS  本地文件 %s", deploy_file)

    def write_failed_files(self) -> None:
        if not self.config.failed_file:
            return

        csv_path = self.config.failed_file
        jsonl_path = csv_path.with_suffix(".jsonl")
        ensure_parent(csv_path)
        ensure_parent(jsonl_path)

        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "groupId",
                    "artifactId",
                    "version",
                    "packaging",
                    "repoType",
                    "stage",
                    "error",
                    "filePath",
                    "pomPath",
                ],
            )
            writer.writeheader()
            for record in self.failures:
                writer.writerow(record.as_failure_row())

        with jsonl_path.open("w", encoding="utf-8") as handle:
            for record in self.failures:
                handle.write(json.dumps(record.as_failure_row(), ensure_ascii=False) + "\n")

    def write_report(self, summary: ReportSummary) -> None:
        if not self.config.report_file:
            return
        ensure_parent(self.config.report_file)
        self.config.report_file.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def log_summary(self, summary: ReportSummary) -> None:
        self.logger.info(
            "SUMMARY  scan=%s filtered=%s release=%s snapshot=%s dryRun=%s success=%s skipped=%s failed=%s validationFailed=%s",
            summary.scan_total,
            summary.filtered_total,
            summary.release_total,
            summary.snapshot_total,
            summary.dry_run,
            summary.deploy_success,
            summary.deploy_skipped,
            summary.deploy_failed,
            summary.validation_failed,
        )
        self.logger.info(
            "执行完成：扫描 %s，命中 %s，成功 %s，跳过 %s，失败 %s，校验失败 %s",
            summary.scan_total,
            summary.filtered_total,
            summary.deploy_success,
            summary.deploy_skipped,
            summary.deploy_failed,
            summary.validation_failed,
        )


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
