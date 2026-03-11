from __future__ import annotations

from urllib.parse import quote

from .config import AppConfig
from .models import ArtifactRecord


def resolve_target_repo(record: ArtifactRecord, config: AppConfig) -> None:
    if config.target_repo_mode == "force-release":
        repo_type = "release"
    elif config.target_repo_mode == "force-snapshot":
        repo_type = "snapshot"
    else:
        repo_type = "snapshot" if (record.version or "").endswith("-SNAPSHOT") else "release"

    record.repo_type = repo_type
    if repo_type == "release":
        record.target_repo_id = config.release_repo_id
        record.target_repo_url = config.release_repo_url.rstrip("/")
    else:
        record.target_repo_id = config.snapshot_repo_id
        record.target_repo_url = config.snapshot_repo_url.rstrip("/")

    group_path = (record.group_id or "").replace(".", "/")
    base_path = f"{group_path}/{record.artifact_id}/{record.version}"
    record.remote_base_path = base_path

    pom_name = f"{record.artifact_id}-{record.version}.pom"
    record.remote_pom_url = f"{record.target_repo_url}/{quote(base_path, safe='/')}/{quote(pom_name)}"

    deploy_file = record.file_for_deploy()
    if deploy_file is not None:
        record.remote_main_url = (
            f"{record.target_repo_url}/{quote(base_path, safe='/')}/{quote(deploy_file.name)}"
        )
