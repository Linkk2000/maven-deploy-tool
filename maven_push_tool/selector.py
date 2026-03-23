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
    return apply_snapshot_history_policy(selected, config, input_patterns)


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


def apply_snapshot_history_policy(
    records: list[ArtifactRecord],
    config: AppConfig,
    input_patterns: list[GavPattern],
) -> list[ArtifactRecord]:
    if config.snapshot_history_mode == "all":
        return records

    explicit_patterns = config.gavs if config.gavs else input_patterns
    explicit_versions = {
        (pattern.group_id, pattern.artifact_id, pattern.version)
        for pattern in explicit_patterns
        if pattern.version
    }

    selected: list[ArtifactRecord] = []
    grouped_snapshots: dict[tuple[str, str], list[ArtifactRecord]] = {}
    for record in records:
        if not (record.version or "").endswith("-SNAPSHOT"):
            selected.append(record)
            continue

        key = (record.group_id or "", record.artifact_id or "", record.version or "")
        if key in explicit_versions:
            selected.append(record)
            continue

        group_key = (record.group_id or "", record.artifact_id or "")
        grouped_snapshots.setdefault(group_key, []).append(record)

    for snapshot_records in grouped_snapshots.values():
        ordered = sort_snapshot_records(snapshot_records)
        if config.snapshot_history_mode == "count":
            selected.extend(ordered[: config.snapshot_history_count])
        else:
            selected.extend(ordered[:1])
    return selected


def sort_snapshot_records(records: list[ArtifactRecord]) -> list[ArtifactRecord]:
    return sorted(
        records,
        key=lambda record: (
            record.version_dir.stat().st_mtime if record.version_dir.exists() else 0,
            record.version or "",
        ),
        reverse=True,
    )
