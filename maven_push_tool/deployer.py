from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .config import AppConfig
from .models import (
    ArtifactRecord,
    DEPLOY_FAILED_DEPLOY,
    DEPLOY_FAILED_RETRY_EXHAUSTED,
    DEPLOY_SUCCESS,
    RuntimeContext,
)
from .parser import build_resolved_deploy_pom


def deploy_record(
    record: ArtifactRecord,
    config: AppConfig,
    runtime: RuntimeContext,
) -> None:
    attempts = config.retry + 1
    last_message = ""
    for attempt in range(1, attempts + 1):
        try:
            result = run_deploy_command(record, config, runtime)
        except FileNotFoundError as exc:
            record.stdout_snippet = None
            record.stderr_snippet = None
            last_message = f"未找到可执行文件: {exc.filename or config.mvn_bin}"
            record.error_stage = "deploy"
            record.error_message = last_message
            record.deploy_status = (
                DEPLOY_FAILED_RETRY_EXHAUSTED if config.retry > 0 else DEPLOY_FAILED_DEPLOY
            )
            return
        except subprocess.TimeoutExpired as exc:
            record.stdout_snippet = trim_output(exc.stdout)
            record.stderr_snippet = trim_output(exc.stderr)
            last_message = f"deploy 超时，timeout={config.timeout}s"
            record.error_stage = "deploy"
            record.error_message = last_message
            if attempt < attempts:
                record.deploy_status = DEPLOY_FAILED_DEPLOY
                continue
            record.deploy_status = (
                DEPLOY_FAILED_RETRY_EXHAUSTED if config.retry > 0 else DEPLOY_FAILED_DEPLOY
            )
            return
        record.stdout_snippet = trim_output(result.stdout)
        record.stderr_snippet = trim_output(result.stderr)
        if result.returncode == 0:
            record.deploy_status = DEPLOY_SUCCESS
            record.error_stage = None
            record.error_message = None
            return
        last_message = build_failure_message(result.returncode, record.stdout_snippet, record.stderr_snippet)
        record.error_stage = "deploy"
        record.error_message = last_message
        if attempt < attempts:
            record.deploy_status = DEPLOY_FAILED_DEPLOY
            continue

    record.deploy_status = (
        DEPLOY_FAILED_RETRY_EXHAUSTED if config.retry > 0 else DEPLOY_FAILED_DEPLOY
    )
    record.error_stage = "deploy"
    record.error_message = last_message or "deploy 失败。"


def build_deploy_command(
    record: ArtifactRecord,
    config: AppConfig,
    runtime: RuntimeContext,
) -> list[str]:
    deploy_file, deploy_pom_file = resolve_deploy_inputs(record, runtime)
    if deploy_file is None:
        raise ValueError("缺少待上传文件。")

    command = [runtime.effective_mvn_bin]
    if runtime.effective_settings_file:
        command.extend(["--settings", str(runtime.effective_settings_file)])
    command.extend(
        [
            "-B",
            "deploy:deploy-file",
            f"-Dfile={deploy_file}",
            f"-DrepositoryId={record.target_repo_id}",
            f"-Durl={record.target_repo_url}",
            f"-Dpackaging={record.packaging}",
        ]
    )
    if deploy_pom_file is not None:
        command.append(f"-DpomFile={deploy_pom_file}")
    return command


def run_deploy_command(
    record: ArtifactRecord,
    config: AppConfig,
    runtime: RuntimeContext,
) -> subprocess.CompletedProcess[str]:
    command = build_deploy_command(record, config, runtime)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=config.timeout,
        check=False,
    )


def trim_output(value: str | None, limit: int = 2000) -> str | None:
    if not value:
        return None
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def resolve_deploy_inputs(
    record: ArtifactRecord,
    runtime: RuntimeContext,
) -> tuple[Path | None, Path | None]:
    if record.pom_path is not None:
        record.deploy_pom_path = ensure_deploy_pom(record, runtime)

    if record.packaging == "pom":
        return record.deploy_pom_path, None
    return record.main_file_path, record.deploy_pom_path


def ensure_deploy_pom(record: ArtifactRecord, runtime: RuntimeContext) -> Path:
    if record.deploy_pom_path is not None and record.deploy_pom_path.exists():
        return record.deploy_pom_path
    if record.pom_path is None:
        raise ValueError("缺少 POM 文件。")

    content = build_resolved_deploy_pom(
        record.pom_path,
        record.group_id or "",
        record.artifact_id or "",
        record.version or "",
        record.packaging or "jar",
        record.local_repo_root,
    )
    fd, temp_name = tempfile.mkstemp(prefix="maven-push-pom-", suffix=".pom")
    os.close(fd)
    temp_path = Path(temp_name)
    temp_path.write_text(content, encoding="utf-8", newline="\n")
    runtime.temp_files.append(temp_path)
    return temp_path


def build_failure_message(
    exit_code: int,
    stdout_snippet: str | None,
    stderr_snippet: str | None,
) -> str:
    parts = [f"deploy 失败，exitCode={exit_code}"]
    if stdout_snippet:
        parts.append(f"stdout={stdout_snippet}")
    if stderr_snippet:
        parts.append(f"stderr={stderr_snippet}")
    return "，".join(parts)
