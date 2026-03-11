from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import AppConfig
from .models import (
    ArtifactRecord,
    DEPLOY_FAILED_VALIDATION,
    VALIDATION_INVALID,
    VALIDATION_VALID,
)


def build_record_from_dir(version_dir: Path, local_repo: Path) -> ArtifactRecord:
    record = ArtifactRecord(local_repo_root=local_repo, version_dir=version_dir)

    pom_files = sorted(version_dir.glob("*.pom"))
    if not pom_files:
        set_invalid(record, "validate", "缺失 POM，V1 默认不上传只有 JAR 的构件。")
        return record

    if len(pom_files) > 1:
        set_invalid(record, "validate", f"发现多个 POM，无法唯一确定目标: {version_dir}")
        return record

    record.pom_path = pom_files[0]
    try:
        pom_info = parse_pom(record.pom_path)
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


def parse_pom(pom_path: Path) -> dict[str, str]:
    tree = ET.parse(pom_path)
    root = tree.getroot()
    project = strip_namespaces(root)

    parent = project.find("parent")
    properties = build_pom_properties(project, parent)

    group_id = resolve_value(
        text_of(project.find("groupId")) or text_of(parent.find("groupId") if parent is not None else None),
        properties,
    )
    version = resolve_value(
        text_of(project.find("version")) or text_of(parent.find("version") if parent is not None else None),
        properties,
    )
    artifact_id = resolve_value(text_of(project.find("artifactId")), properties)
    packaging = resolve_value(text_of(project.find("packaging")) or "jar", properties) or "jar"
    return {
        "group_id": group_id or "",
        "artifact_id": artifact_id or "",
        "version": version or "",
        "packaging": packaging or "jar",
    }


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
    if record.pom_path != expected_pom:
        return f"POM 文件名应为 {expected_pom.name}，实际为 {record.pom_path.name if record.pom_path else '空'}。"
    if record.packaging == "jar":
        expected_main = record.version_dir / f"{prefix}.jar"
        if record.main_file_path != expected_main:
            return f"主 JAR 文件名应为 {expected_main.name}，实际为 {record.main_file_path.name if record.main_file_path else '空'}。"

    return None


def optional_file(path: Path) -> Path | None:
    return path if path.exists() else None


def set_invalid(record: ArtifactRecord, stage: str, message: str) -> None:
    record.validation_status = VALIDATION_INVALID
    record.deploy_status = DEPLOY_FAILED_VALIDATION
    record.error_stage = stage
    record.error_message = message
