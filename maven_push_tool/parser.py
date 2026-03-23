from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .models import (
    ArtifactRecord,
    DEPLOY_FAILED_VALIDATION,
    VALIDATION_INVALID,
    VALIDATION_VALID,
)


@dataclass
class PomModel:
    pom_path: Path
    group_id: str
    artifact_id: str
    version: str
    packaging: str
    parent_group_id: str
    parent_artifact_id: str
    parent_version: str
    properties: dict[str, str]


@dataclass(order=True)
class SnapshotBuild:
    sort_key: tuple[str, int]
    timestamp: str
    build_number: int
    pom_path: Path | None = None
    jar_path: Path | None = None


def build_record_from_dir(version_dir: Path, local_repo: Path, config: AppConfig | None = None) -> ArtifactRecord:
    record = ArtifactRecord(local_repo_root=local_repo, version_dir=version_dir)
    inferred_artifact_id = version_dir.parent.name
    inferred_version = version_dir.name

    if inferred_version.endswith("-SNAPSHOT"):
        return build_snapshot_record(record, inferred_artifact_id, inferred_version, local_repo, config)

    pom_files = sorted(version_dir.glob("*.pom"))
    if not pom_files:
        set_invalid(record, "validate", "缺失 POM，V1 默认不上传只有 JAR 的构件。")
        return record

    if len(pom_files) > 1:
        set_invalid(record, "validate", f"发现多个 POM，无法唯一确定目标: {version_dir}")
        return record

    record.pom_path = pom_files[0]
    try:
        pom_info = parse_pom(record.pom_path, local_repo)
    except Exception as exc:  # pragma: no cover - 防御性分支
        set_invalid(record, "validate", f"POM 解析失败: {exc}")
        return record

    record.group_id = pom_info["group_id"]
    record.artifact_id = pom_info["artifact_id"]
    record.version = pom_info["version"]
    record.packaging = pom_info["packaging"]

    if not record.group_id or not record.artifact_id or not record.version:
        set_invalid(record, "validate", "POM 缺失 groupId/artifactId/version。")
        return record

    prefix = f"{record.artifact_id}-{record.version}"
    record.source_file_path = optional_file(version_dir / f"{prefix}-sources.jar")
    record.javadoc_file_path = optional_file(version_dir / f"{prefix}-javadoc.jar")

    main_jar = version_dir / f"{prefix}.jar"
    if main_jar.exists():
        record.main_file_path = main_jar
        record.file_extension = "jar"
    else:
        record.file_extension = "pom"

    return record


def build_snapshot_record(
    record: ArtifactRecord,
    inferred_artifact_id: str,
    inferred_version: str,
    local_repo: Path,
    config: AppConfig | None,
) -> ArtifactRecord:
    record.snapshot_base_version = inferred_version[: -len("-SNAPSHOT")]

    exact_pom = record.version_dir / f"{inferred_artifact_id}-{inferred_version}.pom"
    selected_pom, selected_build = select_snapshot_pom(
        record.version_dir,
        inferred_artifact_id,
        inferred_version,
        exact_pom,
        config.snapshot_build_mode if config is not None else "latest",
    )
    if selected_pom is None:
        set_invalid(record, "validate", f"snapshot 目录缺失可识别的 POM: {record.version_dir}")
        return record

    record.pom_path = selected_pom
    if selected_build is not None:
        record.snapshot_timestamp = selected_build.timestamp
        record.snapshot_build_number = str(selected_build.build_number)

    try:
        pom_info = parse_pom(record.pom_path, local_repo)
    except Exception as exc:  # pragma: no cover - 防御性分支
        set_invalid(record, "validate", f"POM 解析失败: {exc}")
        return record

    record.group_id = pom_info["group_id"]
    record.artifact_id = pom_info["artifact_id"]
    record.version = pom_info["version"]
    record.packaging = pom_info["packaging"]

    if not record.group_id or not record.artifact_id or not record.version:
        set_invalid(record, "validate", "POM 缺失 groupId/artifactId/version。")
        return record

    prefix = f"{record.artifact_id}-{record.version}"
    record.source_file_path = optional_file(record.version_dir / f"{prefix}-sources.jar")
    record.javadoc_file_path = optional_file(record.version_dir / f"{prefix}-javadoc.jar")

    if record.packaging == "jar":
        exact_jar = record.version_dir / f"{record.artifact_id}-{record.version}.jar"
        selected_jar, jar_build = select_snapshot_main_jar(
            record.version_dir,
            record.artifact_id,
            record.version,
            exact_jar,
            config.snapshot_build_mode if config is not None else "latest",
        )
        if selected_jar is not None:
            record.main_file_path = selected_jar
            record.file_extension = "jar"
            if jar_build is not None:
                record.snapshot_timestamp = jar_build.timestamp
                record.snapshot_build_number = str(jar_build.build_number)
    else:
        record.file_extension = "pom"
    return record


def validate_record(record: ArtifactRecord, config: AppConfig) -> None:
    if record.validation_status == VALIDATION_INVALID:
        record.deploy_status = DEPLOY_FAILED_VALIDATION
        return

    if record.packaging not in {"jar", "pom"}:
        set_invalid(record, "validate", f"V1 暂不支持 packaging={record.packaging}")
        return

    if record.packaging == "pom":
        if record.pom_path is None or not record.pom_path.exists():
            set_invalid(record, "validate", "packaging=pom 但缺失 POM 文件。")
            return
        if record.main_file_path is not None and record.main_file_path.exists():
            record.warnings.append("packaging=pom 但发现同名 jar，已按 pom 上传。")
    elif record.packaging == "jar":
        if record.main_file_path is None or not record.main_file_path.exists():
            set_invalid(record, "validate", "packaging=jar 但缺失主 JAR 文件。")
            return
        if record.pom_path is None or not record.pom_path.exists():
            set_invalid(record, "validate", "packaging=jar 但缺失 POM 文件。")
            return

    path_issue = validate_path_consistency(record)
    if path_issue:
        if config.strict_pom_check:
            set_invalid(record, "validate", path_issue)
            return
        record.warnings.append(path_issue)

    record.validation_status = VALIDATION_VALID


def parse_pom(pom_path: Path, local_repo: Path | None = None) -> dict[str, str]:
    model = load_pom_model(pom_path, local_repo)
    return {
        "group_id": model.group_id,
        "artifact_id": model.artifact_id,
        "version": model.version,
        "packaging": model.packaging,
    }


def build_resolved_deploy_pom(
    pom_path: Path,
    group_id: str,
    artifact_id: str,
    version: str,
    packaging: str,
    local_repo: Path | None = None,
) -> str:
    model = load_pom_model(pom_path, local_repo)
    tree = ET.parse(pom_path)
    project = strip_namespaces(tree.getroot())

    set_or_create_child(project, "groupId", group_id)
    set_or_create_child(project, "artifactId", artifact_id)
    set_or_create_child(project, "version", version)
    set_or_create_child(project, "packaging", packaging)

    parent = project.find("parent")
    if parent is not None:
        set_or_create_child(parent, "groupId", model.parent_group_id)
        set_or_create_child(parent, "artifactId", model.parent_artifact_id)
        set_or_create_child(parent, "version", model.parent_version)

    return ET.tostring(project, encoding="unicode")


def strip_namespaces(root: ET.Element) -> ET.Element:
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]
    return root


def text_of(node: ET.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def build_pom_properties(
    project: ET.Element,
    parent: ET.Element | None,
) -> dict[str, str]:
    properties: dict[str, str] = {}

    parent_group_id = text_of(parent.find("groupId") if parent is not None else None)
    parent_artifact_id = text_of(parent.find("artifactId") if parent is not None else None)
    parent_version = text_of(parent.find("version") if parent is not None else None)

    artifact_id = text_of(project.find("artifactId"))
    packaging = text_of(project.find("packaging")) or "jar"
    group_id = text_of(project.find("groupId")) or parent_group_id
    version = text_of(project.find("version")) or parent_version

    properties.update(
        {
            "project.groupId": group_id or "",
            "pom.groupId": group_id or "",
            "project.artifactId": artifact_id or "",
            "pom.artifactId": artifact_id or "",
            "project.version": version or "",
            "pom.version": version or "",
            "project.packaging": packaging or "jar",
            "pom.packaging": packaging or "jar",
            "project.parent.groupId": parent_group_id or "",
            "parent.groupId": parent_group_id or "",
            "project.parent.artifactId": parent_artifact_id or "",
            "parent.artifactId": parent_artifact_id or "",
            "project.parent.version": parent_version or "",
            "parent.version": parent_version or "",
        }
    )

    properties_node = project.find("properties")
    if properties_node is not None:
        for child in list(properties_node):
            value = text_of(child)
            if value is not None:
                properties[child.tag] = value

    resolved = dict(properties)
    for _ in range(10):
        changed = False
        for key, value in list(resolved.items()):
            expanded = replace_placeholders(value, resolved)
            if expanded != value:
                resolved[key] = expanded
                changed = True
        if not changed:
            break
    return resolved


def load_pom_model(
    pom_path: Path,
    local_repo: Path | None = None,
    seen: set[Path] | None = None,
) -> PomModel:
    normalized_path = pom_path.resolve()
    if seen is None:
        seen = set()
    if normalized_path in seen:
        raise ValueError(f"检测到循环父 POM 引用: {normalized_path}")

    tree = ET.parse(normalized_path)
    project = strip_namespaces(tree.getroot())
    parent = project.find("parent")

    raw_parent_group_id = text_of(parent.find("groupId") if parent is not None else None)
    raw_parent_artifact_id = text_of(parent.find("artifactId") if parent is not None else None)
    raw_parent_version = text_of(parent.find("version") if parent is not None else None)
    raw_artifact_id = text_of(project.find("artifactId"))
    raw_group_id = text_of(project.find("groupId"))
    raw_version = text_of(project.find("version"))
    raw_packaging = text_of(project.find("packaging")) or "jar"

    parent_model = None
    parent_pom_path = resolve_parent_pom_path(
        normalized_path,
        parent,
        local_repo,
        raw_parent_group_id,
        raw_parent_artifact_id,
        raw_parent_version,
    )
    if parent_pom_path is not None and parent_pom_path.exists():
        parent_model = load_pom_model(parent_pom_path, local_repo, seen | {normalized_path})

    properties = build_pom_properties(project, parent)
    if parent_model is not None:
        merged = dict(parent_model.properties)
        merged.update(properties)
        properties = merged

    parent_group_id = parent_model.group_id if parent_model is not None else raw_parent_group_id or ""
    parent_artifact_id = parent_model.artifact_id if parent_model is not None else raw_parent_artifact_id or ""
    parent_version = parent_model.version if parent_model is not None else raw_parent_version or ""

    artifact_id = raw_artifact_id or ""
    group_id = raw_group_id or parent_group_id
    version = raw_version or parent_version
    packaging = raw_packaging or "jar"

    for _ in range(10):
        previous_state = (
            artifact_id,
            group_id,
            version,
            packaging,
            parent_group_id,
            parent_artifact_id,
            parent_version,
            tuple(sorted(properties.items())),
        )
        parent_group_id = resolve_value(parent_group_id, properties) or parent_group_id
        parent_artifact_id = resolve_value(parent_artifact_id, properties) or parent_artifact_id
        parent_version = resolve_value(parent_version, properties) or parent_version

        artifact_id = resolve_value(raw_artifact_id, properties) or artifact_id
        group_id = resolve_value(raw_group_id or parent_group_id, properties) or group_id
        version = resolve_value(raw_version or parent_version, properties) or version
        packaging = resolve_value(raw_packaging or "jar", properties) or packaging

        properties.update(
            {
                "project.groupId": group_id,
                "pom.groupId": group_id,
                "project.artifactId": artifact_id,
                "pom.artifactId": artifact_id,
                "project.version": version,
                "pom.version": version,
                "project.packaging": packaging,
                "pom.packaging": packaging,
                "project.parent.groupId": parent_group_id,
                "parent.groupId": parent_group_id,
                "project.parent.artifactId": parent_artifact_id,
                "parent.artifactId": parent_artifact_id,
                "project.parent.version": parent_version,
                "parent.version": parent_version,
            }
        )

        changed = False
        for key, value in list(properties.items()):
            expanded = replace_placeholders(value, properties)
            if expanded != value:
                properties[key] = expanded
                changed = True

        current_state = (
            artifact_id,
            group_id,
            version,
            packaging,
            parent_group_id,
            parent_artifact_id,
            parent_version,
            tuple(sorted(properties.items())),
        )
        if not changed and current_state == previous_state:
            break

    return PomModel(
        pom_path=normalized_path,
        group_id=group_id,
        artifact_id=artifact_id,
        version=version,
        packaging=packaging,
        parent_group_id=parent_group_id,
        parent_artifact_id=parent_artifact_id,
        parent_version=parent_version,
        properties=properties,
    )


def resolve_value(value: str | None, properties: dict[str, str]) -> str | None:
    if value is None:
        return None
    resolved = value
    for _ in range(10):
        expanded = replace_placeholders(resolved, properties)
        if expanded == resolved:
            break
        resolved = expanded
    return resolved


def replace_placeholders(value: str, properties: dict[str, str]) -> str:
    def replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        return properties.get(key, match.group(0))

    return re.sub(r"\$\{([^}]+)\}", replacer, value)


def set_or_create_child(project: ET.Element, tag: str, value: str) -> None:
    node = project.find(tag)
    if node is None:
        node = ET.SubElement(project, tag)
    node.text = value


def select_snapshot_pom(
    version_dir: Path,
    artifact_id: str,
    version: str,
    exact_pom: Path,
    build_mode: str,
) -> tuple[Path | None, SnapshotBuild | None]:
    if exact_pom.exists():
        return exact_pom, None
    builds = collect_snapshot_builds(version_dir, artifact_id, version)
    if build_mode == "fail-if-multiple" and len(builds) > 1:
        return None, None
    if not builds:
        return None, None
    latest = builds[-1]
    return latest.pom_path, latest


def select_snapshot_main_jar(
    version_dir: Path,
    artifact_id: str,
    version: str,
    exact_jar: Path,
    build_mode: str,
) -> tuple[Path | None, SnapshotBuild | None]:
    if exact_jar.exists():
        return exact_jar, None
    builds = [build for build in collect_snapshot_builds(version_dir, artifact_id, version) if build.jar_path]
    if build_mode == "fail-if-multiple" and len(builds) > 1:
        return None, None
    if not builds:
        return None, None
    latest = builds[-1]
    return latest.jar_path, latest


def collect_snapshot_builds(version_dir: Path, artifact_id: str, version: str) -> list[SnapshotBuild]:
    base_version = version[: -len("-SNAPSHOT")]
    pattern = re.compile(
        rf"^{re.escape(artifact_id)}-{re.escape(base_version)}-(\d{{8}}\.\d{{6}})-(\d+)\.(pom|jar)$"
    )
    builds: dict[tuple[str, int], SnapshotBuild] = {}
    for candidate in version_dir.iterdir():
        if not candidate.is_file():
            continue
        match = pattern.match(candidate.name)
        if not match:
            continue
        timestamp = match.group(1)
        build_number = int(match.group(2))
        extension = match.group(3)
        key = (timestamp, build_number)
        build = builds.get(key)
        if build is None:
            build = SnapshotBuild(
                sort_key=(timestamp, build_number),
                timestamp=timestamp,
                build_number=build_number,
            )
            builds[key] = build
        if extension == "pom":
            build.pom_path = candidate
        elif extension == "jar":
            build.jar_path = candidate
    return sorted(builds.values())


def resolve_parent_pom_path(
    pom_path: Path,
    parent: ET.Element | None,
    local_repo: Path | None,
    parent_group_id: str | None,
    parent_artifact_id: str | None,
    parent_version: str | None,
) -> Path | None:
    if parent is None:
        return None

    relative_path = text_of(parent.find("relativePath"))
    if relative_path is None:
        relative_path = "../pom.xml"

    if relative_path:
        candidate = (pom_path.parent / relative_path).resolve()
        if candidate.exists():
            return candidate

    if not local_repo:
        return None
    if not parent_group_id or not parent_artifact_id or not parent_version:
        return None
    if "${" in parent_group_id or "${" in parent_artifact_id or "${" in parent_version:
        return None

    candidate = (
        local_repo
        / Path(parent_group_id.replace(".", "/"))
        / parent_artifact_id
        / parent_version
        / f"{parent_artifact_id}-{parent_version}.pom"
    )
    return candidate.resolve()


def validate_path_consistency(record: ArtifactRecord) -> str | None:
    try:
        relative_dir = record.version_dir.relative_to(record.local_repo_root)
    except ValueError:
        return "版本目录不在本地仓库根目录下。"

    parts = relative_dir.parts
    if len(parts) < 3:
        return "版本目录层级不足，无法映射为 group/artifact/version。"

    expected_artifact = parts[-2]
    expected_version = parts[-1]
    expected_group = ".".join(parts[:-2])

    if expected_artifact != record.artifact_id:
        return f"路径 artifactId={expected_artifact} 与 POM artifactId={record.artifact_id} 不一致。"
    if expected_version != record.version:
        return f"路径 version={expected_version} 与 POM version={record.version} 不一致。"
    if expected_group != record.group_id:
        return f"路径 groupId={expected_group} 与 POM groupId={record.group_id} 不一致。"

    prefix = f"{record.artifact_id}-{record.version}"
    expected_pom = record.version_dir / f"{prefix}.pom"
    if record.pom_path != expected_pom and not is_valid_snapshot_selected_file(record, record.pom_path, "pom"):
        return f"POM 文件名应为 {expected_pom.name}，实际为 {record.pom_path.name if record.pom_path else '空'}。"
    if record.packaging == "jar":
        expected_main = record.version_dir / f"{prefix}.jar"
        if record.main_file_path != expected_main and not is_valid_snapshot_selected_file(record, record.main_file_path, "jar"):
            return f"主 JAR 文件名应为 {expected_main.name}，实际为 {record.main_file_path.name if record.main_file_path else '空'}。"

    return None


def optional_file(path: Path) -> Path | None:
    return path if path.exists() else None


def is_valid_snapshot_selected_file(
    record: ArtifactRecord,
    path: Path | None,
    extension: str,
) -> bool:
    if path is None or record.version is None or record.artifact_id is None:
        return False
    if not record.version.endswith("-SNAPSHOT") or record.snapshot_base_version is None:
        return False
    pattern = re.compile(
        rf"^{re.escape(record.artifact_id)}-{re.escape(record.snapshot_base_version)}-(\d{{8}}\.\d{{6}})-(\d+)\.{extension}$"
    )
    return bool(pattern.match(path.name))


def set_invalid(record: ArtifactRecord, stage: str, message: str) -> None:
    record.validation_status = VALIDATION_INVALID
    record.deploy_status = DEPLOY_FAILED_VALIDATION
    record.error_stage = stage
    record.error_message = message
