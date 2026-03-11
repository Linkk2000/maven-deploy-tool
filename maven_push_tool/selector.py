from __future__ import annotations

from pathlib import Path

from .config import AppConfig, parse_input_file
from .models import ArtifactRecord, GavPattern


def apply_selection_rules(
    records: list[ArtifactRecord],
    config: AppConfig,
) -> list[ArtifactRecord]:
    input_patterns = parse_input_file(config.input_file) if config.input_file else []
    selected: list[ArtifactRecord] = []
    for record in records:
        selected_by = select_mode(record, config, input_patterns)
        if selected_by is None:
            continue
        if not passes_common_filters(record, config):
            continue
        record.selected_by = selected_by
        selected.append(record)
    return selected


def select_mode(
    record: ArtifactRecord,
    config: AppConfig,
    input_patterns: list[GavPattern],
) -> str | None:
    if config.gavs:
        return "gav" if match_gav(record, config.gavs) else None
    if input_patterns:
        return "input-file" if match_gav(record, input_patterns) else None
    if config.group_prefixes:
        return "group-prefix" if match_group_prefix(record, config.group_prefixes) else None
    if config.scan_subpath:
        return "scan-subpath" if match_scan_subpath(record, config.scan_subpath) else None
    if config.scan_all:
        return "all"
    return None


def passes_common_filters(record: ArtifactRecord, config: AppConfig) -> bool:
    if record.group_id and match_group_prefix(record, config.exclude_group_prefixes):
        return False
    if record.packaging and record.packaging not in config.packaging:
        return False
    if not config.include_classifier and record.classifier:
        return False
    return True


def match_gav(record: ArtifactRecord, patterns: list[GavPattern]) -> bool:
    return any(pattern.matches(record) for pattern in patterns)


def match_group_prefix(record: ArtifactRecord, prefixes: list[str]) -> bool:
    if not record.group_id:
        return False
    return any(record.group_id.startswith(prefix) for prefix in prefixes)


def match_scan_subpath(record: ArtifactRecord, scan_subpath: str) -> bool:
    try:
        relative = record.version_dir.relative_to(record.local_repo_root)
    except ValueError:
        return False
    normalized = relative.as_posix()
    target = scan_subpath.strip("/")
    return normalized.startswith(target)
