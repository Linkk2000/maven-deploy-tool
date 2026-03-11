from __future__ import annotations

import subprocess

from .config import AppConfig
from .models import (
    ArtifactRecord,
    DEPLOY_FAILED_DEPLOY,
    DEPLOY_FAILED_RETRY_EXHAUSTED,
    DEPLOY_SUCCESS,
    RuntimeContext,
)


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
        last_message = (
            f"deploy 失败，exitCode={result.returncode}"
            f"{'，stderr=' + record.stderr_snippet if record.stderr_snippet else ''}"
        )
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
    deploy_file = record.file_for_deploy()
    if deploy_file is None:
        raise ValueError("缺少待上传文件。")

    command = [config.mvn_bin]
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
    if record.packaging != "pom" and record.pom_path is not None:
        command.append(f"-DpomFile={record.pom_path}")
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
