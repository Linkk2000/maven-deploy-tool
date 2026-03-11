from __future__ import annotations

from pathlib import Path

IGNORED_SUFFIXES = {
    ".sha1",
    ".md5",
    ".sha256",
    ".sha512",
    ".asc",
    ".lastUpdated",
}
IGNORED_FILES = {"_remote.repositories", "resolver-status.properties"}


def scan_version_dirs(local_repo: Path) -> list[Path]:
    version_dirs: list[Path] = []
    for current_dir, _, filenames in __import__("os").walk(local_repo):
        current_path = Path(current_dir)
        if is_candidate_version_dir(current_path, filenames):
            version_dirs.append(current_path)
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
