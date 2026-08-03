"""Cleanup of abandoned, application-owned temporary files."""

import re
from pathlib import Path

_ATOMIC_TEMP = re.compile(r"^\.(?P<target>.+)\.[A-Za-z0-9_]{8}\.tmp$")
_TAG_STORE_TEMP = re.compile(r"^tag-store-[A-Za-z0-9_]{8}\.sqlite(?:-(?:wal|shm))?$")

_ATOMIC_LOCATIONS = {
    "": {"README.md"},
    "data": {".parquet"},
    "assets": {".parquet"},
    "manifests": {".manifest.json"},
    "asset-manifests": {".assets.manifest.json"},
    "statistics": {".json"},
}


def _is_owned_atomic_temp(path: Path, directory: str) -> bool:
    match = _ATOMIC_TEMP.fullmatch(path.name)
    if match is None:
        return False
    return any(match.group("target").endswith(suffix) for suffix in _ATOMIC_LOCATIONS[directory])


def cleanup_stale_temps(data_root: Path) -> tuple[Path, ...]:
    """Remove only abandoned temporary files created by this pipeline.

    This runs at the beginning of a new pipeline invocation, after the prior
    invocation has stopped. Unknown files are intentionally left untouched so
    publication validation can continue to reject them.
    """
    removed: list[Path] = []
    for directory_name in _ATOMIC_LOCATIONS:
        directory = data_root / directory_name if directory_name else data_root
        if not directory.is_dir() or directory.is_symlink():
            continue
        for candidate in directory.iterdir():
            if (
                candidate.is_file()
                and not candidate.is_symlink()
                and _is_owned_atomic_temp(candidate, directory_name)
            ):
                candidate.unlink()
                removed.append(candidate)
    temporary_root = data_root / "tmp"
    if temporary_root.is_dir() and not temporary_root.is_symlink():
        for candidate in temporary_root.iterdir():
            if (
                candidate.is_file()
                and not candidate.is_symlink()
                and _TAG_STORE_TEMP.fullmatch(candidate.name)
            ):
                candidate.unlink()
                removed.append(candidate)
    return tuple(sorted(removed, key=lambda path: path.as_posix()))


__all__ = ["cleanup_stale_temps"]
