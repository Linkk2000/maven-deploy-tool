from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


VALIDATION_PENDING = "PENDING"
VALIDATION_VALID = "VALID"
VALIDATION_INVALID = "INVALID"

PRECHECK_SKIPPED = "SKIPPED"
PRECHECK_EXISTS = "EXISTS"
PRECHECK_NOT_FOUND = "NOT_FOUND"
PRECHECK_FAILED = "CHECK_FAILED"

DEPLOY_PENDING = "PENDING"
DEPLOY_DRY_RUN = "DRY_RUN"
DEPLOY_SKIPPED_EXISTING = "SKIPPED_EXISTING"
DEPLOY_SUCCESS = "SUCCESS"
DEPLOY_FAILED_VALIDATION = "FAILED_VALIDATION"
DEPLOY_FAILED_PRECHECK = "FAILED_PRECHECK"
DEPLOY_FAILED_DEPLOY = "FAILED_DEPLOY"
DEPLOY_FAILED_RETRY_EXHAUSTED = "FAILED_RETRY_EXHAUSTED"


@dataclass
class ArtifactRecord:
    local_repo_root: Path
    version_dir: Path
    group_id: Optional[str] = None
    artifact_id: Optional[str] = None
    version: Optional[str] = None
    packaging: Optional[str] = None
    classifier: Optional[str] = None
    pom_path: Optional[Path] = None
    deploy_pom_path: Optional[Path] = None
    main_file_path: Optional[Path] = None
    source_file_path: Optional[Path] = None
    javadoc_file_path: Optional[Path] = None
    file_extension: Optional[str] = None
    repo_type: Optional[str] = None
    target_repo_id: Optional[str] = None
    target_repo_url: Optional[str] = None
    remote_base_path: Optional[str] = None
    remote_main_url: Optional[str] = None
    remote_pom_url: Optional[str] = None
    selected_by: Optional[str] = None
    validation_status: str = VALIDATION_PENDING
    precheck_status: str = PRECHECK_SKIPPED
    deploy_status: str = DEPLOY_PENDING
    error_stage: Optional[str] = None
    error_message: Optional[str] = None
    stdout_snippet: Optional[str] = None
    stderr_snippet: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    def gav(self) -> str:
        group_id = self.group_id or "?"
        artifact_id = self.artifact_id or "?"
        version = self.version or "?"
        return f"{group_id}:{artifact_id}:{version}"

    def file_for_deploy(self) -> Optional[Path]:
        if self.packaging == "pom":
            return self.pom_path
        return self.main_file_path

    def as_failure_row(self) -> dict[str, str]:
        return {
            "groupId": self.group_id or "",
            "artifactId": self.artifact_id or "",
            "version": self.version or "",
            "packaging": self.packaging or "",
            "repoType": self.repo_type or "",
            "stage": self.error_stage or "",
            "error": self.error_message or "",
            "filePath": str(self.main_file_path or ""),
            "pomPath": str(self.pom_path or ""),
        }

    def as_json(self) -> dict[str, object]:
        data = asdict(self)
        for key, value in list(data.items()):
            if isinstance(value, Path):
                data[key] = str(value)
        return data


@dataclass
class GavPattern:
    group_id: str
    artifact_id: str
    version: Optional[str] = None

    def matches(self, record: ArtifactRecord) -> bool:
        if record.group_id != self.group_id or record.artifact_id != self.artifact_id:
            return False
        if self.version is None:
            return True
        return record.version == self.version


@dataclass
class SettingsServer:
    server_id: str
    username: Optional[str] = None
    password: Optional[str] = None


@dataclass
class SettingsInfo:
    path: Optional[Path] = None
    local_repository: Optional[Path] = None
    servers: dict[str, SettingsServer] = field(default_factory=dict)


@dataclass
class RuntimeContext:
    local_repo: Path
    settings_info: SettingsInfo
    effective_settings_file: Optional[Path]
    effective_mvn_bin: str
    temp_files: list[Path] = field(default_factory=list)


@dataclass
class ReportSummary:
    scan_total: int = 0
    filtered_total: int = 0
    release_total: int = 0
    snapshot_total: int = 0
    dry_run: bool = False
    precheck_exists: int = 0
    precheck_not_found: int = 0
    precheck_failed: int = 0
    deploy_success: int = 0
    deploy_skipped: int = 0
    deploy_failed: int = 0
    validation_failed: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "scanTotal": self.scan_total,
            "filteredTotal": self.filtered_total,
            "releaseTotal": self.release_total,
            "snapshotTotal": self.snapshot_total,
            "dryRun": self.dry_run,
            "precheckExists": self.precheck_exists,
            "precheckNotFound": self.precheck_not_found,
            "precheckFailed": self.precheck_failed,
            "deploySuccess": self.deploy_success,
            "deploySkipped": self.deploy_skipped,
            "deployFailed": self.deploy_failed,
            "validationFailed": self.validation_failed,
        }
