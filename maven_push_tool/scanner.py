from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig, parse_input_file
from .models import GavPattern

IGNORED_SUFFIXES = {
    ".sha1",
    ".md5",
    ".sha256",
    ".sha512",
    ".asc",
    ".lastUpdated",
}
IGNORED_FILES = {"_remote.repositories", "resolver-status.properties"}


@dataclass
class ScanPlan:
    mode: str
    roots: list[Path]


def build_scan_plan(local_repo: Path, config: AppConfig) -> ScanPlan:
    input_patterns = parse_input_file(config.input_file) if config.input_file else []

    if config.gavs:
        return ScanPlan("gav", normalize_roots(local_repo, roots_for_gav_patterns(local_repo, config.gavs)))
    if input_patterns:
        return ScanPlan(
            "input-file",
            normalize_roots(local_repo, roots_for_gav_patterns(local_repo, input_patterns)),
        )
    if config.group_prefixes:
        return ScanPlan(
            "group-prefix",
            normalize_roots(local_repo, roots_for_group_prefixes(local_repo, config.group_prefixes)),
        )
    if config.scan_subpath:
        return ScanPlan(
            "scan-subpath",
            normalize_roots(local_repo, [local_repo / Path(config.scan_subpath)]),
        )
    return ScanPlan("all", [local_repo])


def scan_version_dirs(scan_roots: list[Path]) -> list[Path]:
    version_dirs: list[Path] = []
    seen: set[Path] = set()
    for scan_root in scan_roots:
        if scan_root.is_file():
            continue
        for current_dir, _, filenames in __import__("os").walk(scan_root):
            current_path = Path(current_dir)
            if current_path in seen:
                continue
            if is_candidate_version_dir(current_path, filenames):
                version_dirs.append(current_path)
                seen.add(current_path)
    version_dirs.sort()
    return version_dirs


def is_candidate_version_dir(version_dir: Path, filenames: list[str]) -> bool:
    useful_files = [name for name in filenames if is_candidate_file(name)]
    if not useful_files:
        return False
    return any(name.endswith(".pom") or name.endswith(".jar") for name in useful_files)


def is_candidate_file(filename: str) -> bool:
    if filename in IGNORED_FILES:
        return False
    if filename.startswith("maven-metadata") and filename.endswith(".xml"):
        return False
    for suffix in IGNORED_SUFFIXES:
        if filename.endswith(suffix):
            return False
    return True


def roots_for_gav_patterns(local_repo: Path, patterns: list[GavPattern]) -> list[Path]:
    roots: list[Path] = []
    for pattern in patterns:
        base_dir = local_repo / Path(pattern.group_id.replace(".", "/")) / pattern.artifact_id
        if pattern.version:
            roots.append(base_dir / pattern.version)
        else:
            roots.append(base_dir)
    return roots


def roots_for_group_prefixes(local_repo: Path, prefixes: list[str]) -> list[Path]:
    return [local_repo / Path(prefix.replace(".", "/")) for prefix in prefixes]


def normalize_roots(local_repo: Path, roots: list[Path]) -> list[Path]:
    existing_roots = []
    for root in roots:
        normalized = root.resolve()
        if normalized.exists():
            existing_roots.append(normalized)

    unique_roots: list[Path] = []
    for root in sorted(set(existing_roots)):
        if any(is_subpath(root, parent) for parent in unique_roots):
            continue
        unique_roots = [parent for parent in unique_roots if not is_subpath(parent, root)]
        unique_roots.append(root)

    if unique_roots:
        return unique_roots
    return [] if roots else [local_repo.resolve()]


def is_subpath(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
