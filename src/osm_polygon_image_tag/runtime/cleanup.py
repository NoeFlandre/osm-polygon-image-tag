"""Cleanup of abandoned, application-owned temporary files."""

import re
from collections.abc import Callable
from pathlib import Path

_ATOMIC_TEMP = re.compile(r"^\.(?P<target>.+)\.[A-Za-z0-9_]{8}\.tmp$")
_ASSET_SORT_TEMP = re.compile(r"^\.asset-sort\.[A-Za-z0-9_]{8}\.sqlite(?:-journal)?$")
_PUBLIC_ASSET_LEGACY_TEMP = re.compile(
    r"^\.public-assets\.[A-Za-z0-9_]{8}\.sqlite(?:-(?:journal|wal|shm))?$"
)
_TAG_STORE_TEMP = re.compile(r"^tag-store-[A-Za-z0-9_]{8}\.sqlite(?:-(?:wal|shm))?$")

_ATOMIC_LOCATIONS = {
    "": {"README.md"},
    "data": {".parquet"},
    "assets": {".parquet"},
    "manifests": {".manifest.json"},
    "asset-manifests": {".assets.manifest.json"},
    "public": {".parquet", "public-manifest"},
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
    removed = [
        candidate
        for directory_name in _ATOMIC_LOCATIONS
        for candidate in _cleanup_directory(data_root, directory_name)
    ]
    removed.extend(_cleanup_directory(data_root, "tmp"))
    return tuple(sorted(removed, key=lambda path: path.as_posix()))


def _cleanup_directory(data_root: Path, directory_name: str) -> list[Path]:
    directory = data_root / directory_name if directory_name else data_root
    if not directory.is_dir() or directory.is_symlink():
        return []
    if directory_name == "tmp":
        return _remove_matching(directory, _is_tmp_temp)
    return _remove_matching(directory, lambda path: _is_pipeline_temp(path, directory_name))


def _remove_matching(directory: Path, predicate: Callable[[Path], bool]) -> list[Path]:
    removed: list[Path] = []
    for candidate in directory.iterdir():
        if _is_real_file(candidate) and predicate(candidate):
            candidate.unlink()
            removed.append(candidate)
    return removed


def _is_real_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _is_pipeline_temp(path: Path, directory: str) -> bool:
    return _is_owned_atomic_temp(path, directory) or (
        directory == "assets" and _ASSET_SORT_TEMP.fullmatch(path.name) is not None
    )


def _is_tmp_temp(path: Path) -> bool:
    return (
        _TAG_STORE_TEMP.fullmatch(path.name) is not None
        or _PUBLIC_ASSET_LEGACY_TEMP.fullmatch(path.name) is not None
    )


__all__ = ["cleanup_stale_temps"]
