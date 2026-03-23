from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .models import GavPattern, RuntimeContext, SettingsInfo, SettingsServer

SUPPORTED_PACKAGING = {"jar", "pom"}
TARGET_REPO_MODES = {"auto", "force-release", "force-snapshot"}


@dataclass
class AppConfig:
    local_repo: Optional[Path]
    settings_file: Optional[Path]
    mvn_bin: str
    release_repo_id: str
    release_repo_url: str
    snapshot_repo_id: str
    snapshot_repo_url: str
    username: Optional[str]
    password: Optional[str]
    auth_from_settings: bool
    scan_all: bool
    group_prefixes: list[str]
    exclude_group_prefixes: list[str]
    gavs: list[GavPattern]
    input_file: Optional[Path]
    scan_subpath: Optional[str]
    packaging: set[str]
    include_classifier: bool
    snapshot_history_mode: str
    snapshot_history_count: int
    snapshot_build_mode: str
    dry_run: bool
    threads: int
    retry: int
    timeout: int
    continue_on_error: bool
    stop_on_first_error: bool
    release_precheck: bool
    skip_existing: bool
    fail_on_precheck_error: bool
    log_file: Optional[Path]
    failed_file: Optional[Path]
    report_file: Optional[Path]
    append_log: bool
    log_level: str
    allow_redeploy: bool
    target_repo_mode: str
    strict_pom_check: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从本地 Maven 仓库批量补推构件到远程仓库。"
    )
    parser.add_argument("--local-repo", type=Path)
    parser.add_argument("--settings-file", type=Path)
    parser.add_argument("--mvn-bin", default="mvn")
    parser.add_argument("--release-repo-id", required=True)
    parser.add_argument("--release-repo-url", required=True)
    parser.add_argument("--snapshot-repo-id", required=True)
    parser.add_argument("--snapshot-repo-url", required=True)
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument(
        "--auth-from-settings",
        default=True,
        action=argparse.BooleanOptionalAction,
    )

    parser.add_argument("--all", dest="scan_all", action="store_true")
    parser.add_argument("--group-prefix", action="append", default=[])
    parser.add_argument("--exclude-group-prefix", action="append", default=[])
    parser.add_argument("--gav", action="append", default=[])
    parser.add_argument("--input-file", type=Path)
    parser.add_argument("--scan-subpath")
    parser.add_argument("--packaging", default="jar,pom")
    parser.add_argument(
        "--include-classifier",
        default=False,
        action=argparse.BooleanOptionalAction,
    )
    parser.add_argument(
        "--snapshot-history-mode",
        default="all",
        choices=["latest", "all", "count"],
    )
    parser.add_argument("--snapshot-history-count", type=int, default=1)
    parser.add_argument(
        "--snapshot-build-mode",
        default="latest",
        choices=["latest", "fail-if-multiple"],
    )

    parser.add_argument(
        "--dry-run",
        default=True,
        action=argparse.BooleanOptionalAction,
    )
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--retry", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--continue-on-error",
        default=True,
        action=argparse.BooleanOptionalAction,
    )
    parser.add_argument(
        "--stop-on-first-error",
        default=False,
        action=argparse.BooleanOptionalAction,
    )

    parser.add_argument(
        "--release-precheck",
        default=True,
        action=argparse.BooleanOptionalAction,
    )
    parser.add_argument(
        "--skip-existing",
        default=True,
        action=argparse.BooleanOptionalAction,
    )
    parser.add_argument(
        "--fail-on-precheck-error",
        default=True,
        action=argparse.BooleanOptionalAction,
    )

    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--failed-file", type=Path)
    parser.add_argument("--report-file", type=Path)
    parser.add_argument("--append-log", action="store_true")
    parser.add_argument("--log-level", default="INFO")

    parser.add_argument(
        "--allow-redeploy",
        default=False,
        action=argparse.BooleanOptionalAction,
    )
    parser.add_argument(
        "--target-repo-mode",
        default="auto",
        choices=sorted(TARGET_REPO_MODES),
    )
    parser.add_argument(
        "--strict-pom-check",
        default=True,
        action=argparse.BooleanOptionalAction,
    )
    return parser


def load_config(argv: Optional[list[str]] = None) -> AppConfig:
    args = build_parser().parse_args(argv)
    packaging = parse_packaging(args.packaging)
    gavs = parse_gav_list(args.gav)
    return AppConfig(
        local_repo=normalize_cli_path(args.local_repo),
        settings_file=normalize_cli_path(args.settings_file),
        mvn_bin=normalize_cli_command_path(args.mvn_bin),
        release_repo_id=args.release_repo_id,
        release_repo_url=args.release_repo_url,
        snapshot_repo_id=args.snapshot_repo_id,
        snapshot_repo_url=args.snapshot_repo_url,
        username=args.username,
        password=args.password,
        auth_from_settings=args.auth_from_settings,
        scan_all=args.scan_all,
        group_prefixes=args.group_prefix,
        exclude_group_prefixes=args.exclude_group_prefix,
        gavs=gavs,
        input_file=normalize_cli_path(args.input_file),
        scan_subpath=args.scan_subpath,
        packaging=packaging,
        include_classifier=args.include_classifier,
        snapshot_history_mode=args.snapshot_history_mode,
        snapshot_history_count=args.snapshot_history_count,
        snapshot_build_mode=args.snapshot_build_mode,
        dry_run=args.dry_run,
        threads=args.threads,
        retry=args.retry,
        timeout=args.timeout,
        continue_on_error=args.continue_on_error,
        stop_on_first_error=args.stop_on_first_error,
        release_precheck=args.release_precheck,
        skip_existing=args.skip_existing,
        fail_on_precheck_error=args.fail_on_precheck_error,
        log_file=normalize_cli_path(args.log_file),
        failed_file=normalize_cli_path(args.failed_file),
        report_file=normalize_cli_path(args.report_file),
        append_log=args.append_log,
        log_level=args.log_level.upper(),
        allow_redeploy=args.allow_redeploy,
        target_repo_mode=args.target_repo_mode,
        strict_pom_check=args.strict_pom_check,
    )


def validate_config(config: AppConfig) -> None:
    if not config.release_repo_url.strip():
        raise ValueError("必须提供 --release-repo-url。")
    if not config.snapshot_repo_url.strip():
        raise ValueError("必须提供 --snapshot-repo-url。")
    if config.threads < 1:
        raise ValueError("--threads 不能小于 1。")
    if config.retry < 0:
        raise ValueError("--retry 不能小于 0。")
    if config.timeout < 1:
        raise ValueError("--timeout 必须大于 0。")
    if not config.packaging:
        raise ValueError("--packaging 至少要包含一种类型。")
    if config.snapshot_history_count < 1:
        raise ValueError("--snapshot-history-count 不能小于 1。")
    unsupported = config.packaging.difference(SUPPORTED_PACKAGING)
    if unsupported:
        joined = ",".join(sorted(unsupported))
        raise ValueError(f"V1 仅支持 jar,pom，发现不支持的 packaging: {joined}")
    if config.input_file and not config.input_file.exists():
        raise ValueError(f"--input-file 不存在: {config.input_file}")
    if config.settings_file and not config.settings_file.exists():
        raise ValueError(f"--settings-file 不存在: {config.settings_file}")
    if config.username and config.password is None:
        raise ValueError("传入 --username 时必须同时提供 --password。")
    if any(ch in {"\\", os.sep} for ch in (config.scan_subpath or "")):
        config.scan_subpath = normalize_subpath(config.scan_subpath)
    ensure_selection_present(config)


def ensure_selection_present(config: AppConfig) -> None:
    if config.gavs or config.input_file or config.group_prefixes or config.scan_subpath or config.scan_all:
        return
    raise ValueError("必须提供至少一种筛选方式：--gav / --input-file / --group-prefix / --scan-subpath / --all")


def parse_packaging(value: str) -> set[str]:
    items = {item.strip() for item in value.split(",") if item.strip()}
    return items


def parse_gav_list(values: Iterable[str]) -> list[GavPattern]:
    patterns: list[GavPattern] = []
    for value in values:
        parts = [part.strip() for part in value.split(":")]
        if len(parts) not in {2, 3} or not all(parts[:2]):
            raise ValueError(f"非法 GAV: {value}")
        version = parts[2] if len(parts) == 3 and parts[2] else None
        patterns.append(GavPattern(parts[0], parts[1], version))
    return patterns


def parse_input_file(path: Path) -> list[GavPattern]:
    values: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        values.append(stripped)
    return parse_gav_list(values)


def read_settings_file(path: Optional[Path]) -> SettingsInfo:
    if path is None:
        path = default_settings_path()
    if path is None or not path.exists():
        return SettingsInfo(path=path)

    tree = ET.parse(path)
    root = strip_namespaces(tree.getroot())
    local_repository = text_of(root.find("./localRepository"))
    servers: dict[str, SettingsServer] = {}
    for server_node in root.findall("./servers/server"):
        server_id = text_of(server_node.find("id"))
        if not server_id:
            continue
        servers[server_id] = SettingsServer(
            server_id=server_id,
            username=text_of(server_node.find("username")),
            password=text_of(server_node.find("password")),
        )
    return SettingsInfo(
        path=path,
        local_repository=Path(local_repository).expanduser() if local_repository else None,
        servers=servers,
    )


def resolve_runtime_context(config: AppConfig) -> RuntimeContext:
    settings_info = read_settings_file(config.settings_file)
    local_repo = resolve_local_repo(config, settings_info)
    if not local_repo.exists():
        raise ValueError(f"本地仓库不存在: {local_repo}")
    effective_mvn_bin = resolve_maven_binary(config.mvn_bin)

    effective_settings = settings_info.path if settings_info.path and settings_info.path.exists() else None
    temp_files: list[Path] = []
    if config.username and config.password:
        generated = generate_temp_settings(config, settings_info, local_repo)
        effective_settings = generated
        temp_files.append(generated)
    return RuntimeContext(
        local_repo=local_repo,
        settings_info=settings_info,
        effective_settings_file=effective_settings,
        effective_mvn_bin=effective_mvn_bin,
        temp_files=temp_files,
    )


def resolve_local_repo(config: AppConfig, settings_info: SettingsInfo) -> Path:
    if config.local_repo:
        return config.local_repo.expanduser().resolve()
    if settings_info.local_repository:
        return normalize_cli_path(settings_info.local_repository).expanduser().resolve()
    return (Path.home() / ".m2" / "repository").resolve()


def generate_temp_settings(
    config: AppConfig,
    settings_info: SettingsInfo,
    local_repo: Path,
) -> Path:
    if settings_info.path and settings_info.path.exists():
        tree = ET.parse(settings_info.path)
        settings = strip_namespaces(tree.getroot())
    else:
        settings = ET.Element("settings")
        settings.set("xmlns", "http://maven.apache.org/SETTINGS/1.0.0")
        settings.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
        settings.set(
            "xsi:schemaLocation",
            "http://maven.apache.org/SETTINGS/1.0.0 "
            "https://maven.apache.org/xsd/settings-1.0.0.xsd",
        )
        tree = ET.ElementTree(settings)

    local_repo_node = settings.find("./localRepository")
    if local_repo_node is None:
        local_repo_node = ET.SubElement(settings, "localRepository")
    local_repo_node.text = str(local_repo)

    servers_node = settings.find("./servers")
    if servers_node is None:
        servers_node = ET.SubElement(settings, "servers")
    else:
        for child in list(servers_node):
            servers_node.remove(child)

    combined = dict(settings_info.servers)
    for repo_id in (config.release_repo_id, config.snapshot_repo_id):
        combined[repo_id] = SettingsServer(
            server_id=repo_id,
            username=config.username,
            password=config.password,
        )
    for server in combined.values():
        server_node = ET.SubElement(servers_node, "server")
        ET.SubElement(server_node, "id").text = server.server_id
        if server.username is not None:
            ET.SubElement(server_node, "username").text = server.username
        if server.password is not None:
            ET.SubElement(server_node, "password").text = server.password

    fd, temp_name = tempfile.mkstemp(prefix="maven-push-", suffix="-settings.xml")
    os.close(fd)
    temp_path = Path(temp_name)
    tree.write(temp_path, encoding="utf-8", xml_declaration=True)
    return temp_path


def default_settings_path() -> Optional[Path]:
    candidate = normalize_cli_path(Path.home() / ".m2" / "settings.xml")
    return candidate


def text_of(node: Optional[ET.Element]) -> Optional[str]:
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def strip_namespaces(root: ET.Element) -> ET.Element:
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]
    return root


def normalize_subpath(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    return value.replace("\\", "/").strip("/")


def normalize_cli_path(value: Path | str | None) -> Path | None:
    if value is None:
        return None
    raw = str(value)
    if not raw:
        return None
    return Path(convert_windows_bash_path(raw))


def normalize_cli_command_path(value: str) -> str:
    return convert_windows_bash_path(value)


def convert_windows_bash_path(value: str) -> str:
    if os.name != "nt":
        return value
    stripped = value.strip()
    match = re.match(r"^/([a-zA-Z])/(.*)$", stripped)
    if match:
        drive = match.group(1).upper()
        rest = match.group(2).replace("/", "\\")
        return f"{drive}:\\{rest}"
    return stripped


def resolve_maven_binary(mvn_bin: str) -> str:
    candidate = normalize_cli_command_path(mvn_bin)

    if os.path.isabs(candidate) or any(sep in candidate for sep in ("\\", "/")):
        if Path(candidate).exists():
            return str(Path(candidate))
        raise ValueError(f"未找到 Maven 可执行文件: {candidate}")

    resolved = shutil.which(candidate)
    if resolved:
        return resolved

    if os.name == "nt":
        for suffix in (".cmd", ".bat", ".exe"):
            resolved = shutil.which(candidate + suffix)
            if resolved:
                return resolved

    raise ValueError(
        "未找到 Maven 可执行文件，请检查 PATH 或通过 --mvn-bin 显式指定，例如 "
        "--mvn-bin D:/apache-maven/bin/mvn.cmd"
    )
