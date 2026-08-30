"""Checkpoint selection and safety rules for public-asset materialization."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from osm_polygon_image_tag.artifacts.checkpoints import remove_checkpoint_files
from osm_polygon_image_tag.core.atomic import promote_temporary_file, temporary_file_path

PUBLIC_ASSET_CHECKPOINT_FILENAME = ".public-assets.sqlite"
PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE = f"tmp/{PUBLIC_ASSET_CHECKPOINT_FILENAME}"
PUBLIC_ASSET_DEDUP_CHECKPOINT_SCHEMA_VERSION = 1
PUBLIC_ASSET_SQLITE_CACHE_KIB = 131_072
PUBLIC_ASSET_SQLITE_MMAP_BYTES = 256 * 1024**2
PUBLIC_ASSET_SQLITE_PAGE_SIZE = 65_536
PUBLIC_ASSET_CHECKPOINT_MIN_FREE_BYTES = 8 * 1024**3
PUBLIC_ASSET_CHECKPOINT_MAX_FILE_BYTES = (15 * 1024**3) // 2


def _remove_legacy_checkpoints(temporary_root: Path, current: Path) -> None:
    for path in _legacy_checkpoint_paths(temporary_root, current):
        path.unlink(missing_ok=True)


def _legacy_checkpoint_paths(temporary_root: Path, current: Path) -> Iterator[Path]:
    for path in temporary_root.glob(".public-assets.*.sqlite*"):
        if path != current:
            yield path


def _copy_clean_checkpoint(source: Path, destination: Path) -> None:
    """Seed a local checkpoint from a clean durable database atomically."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with temporary_file_path(
        destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    ) as temporary_path:
        shutil.copyfile(source, temporary_path)
        promote_temporary_file(temporary_path, destination, sync_directory=True)


def _checkpoint_family(path: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{path}{suffix}") for suffix in ("", "-journal", "-wal", "-shm"))


def _validate_checkpoint_root(data_root: Path, checkpoint_root: Path) -> Path:
    if checkpoint_root.exists() and checkpoint_root.is_symlink():
        raise ValueError("asset checkpoint root must not be a symlink")
    scratch_root = checkpoint_root.expanduser().resolve()
    data_resolved = data_root.resolve()
    if _checkpoint_root_overlaps(scratch_root, data_resolved):
        raise ValueError("asset checkpoint root must be separate from the data root")
    scratch_root.mkdir(parents=True, exist_ok=True)
    return scratch_root


def _seed_external_checkpoint(durable: Path, scratch: Path) -> None:
    scratch_family = _checkpoint_family(scratch)
    durable_family = _checkpoint_family(durable)
    if _can_seed_external_checkpoint(scratch_family, durable, durable_family):
        _checkpoint_max_bytes(scratch, initial_bytes=durable.stat().st_size)
        _copy_clean_checkpoint(durable, scratch)


def _checkpoint_root_overlaps(scratch: Path, data_root: Path) -> bool:
    return scratch == data_root or scratch in data_root.parents or data_root in scratch.parents


def _can_seed_external_checkpoint(
    scratch_family: Sequence[Path], durable: Path, durable_family: Sequence[Path]
) -> bool:
    return (
        not any(path.exists() for path in scratch_family)
        and durable.is_file()
        and not any(path.exists() for path in durable_family[1:])
    )


def _active_checkpoint(durable: Path, scratch: Path) -> Path:
    scratch_family = _checkpoint_family(scratch)
    durable_family = _checkpoint_family(durable)
    return (
        scratch
        if any(path.exists() for path in scratch_family)
        or not any(path.exists() for path in durable_family[1:])
        else durable
    )


def _prepare_checkpoint_paths(
    data_root: Path, checkpoint_root: Path | None
) -> tuple[Path, tuple[Path, ...]]:
    """Choose the active checkpoint and all copies cleaned after success."""
    durable = data_root.resolve() / PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE
    if checkpoint_root is None:
        return durable, (durable,)

    scratch_root = _validate_checkpoint_root(data_root, checkpoint_root)
    scratch = scratch_root / PUBLIC_ASSET_CHECKPOINT_FILENAME
    if scratch.is_symlink():
        raise ValueError("asset checkpoint file must not be a symlink")
    _seed_external_checkpoint(durable, scratch)
    active = _active_checkpoint(durable, scratch)
    cleanup = (active, scratch if active != scratch else durable)
    return active, cleanup


def _checkpoint_max_bytes(path: Path, *, initial_bytes: int = 0) -> int:
    """Return a conservative file limit for an external checkpoint."""
    free_bytes = shutil.disk_usage(path.parent).free
    current_bytes = path.stat().st_size if path.is_file() else initial_bytes
    max_bytes = _checkpoint_limit(free_bytes, current_bytes)
    _validate_checkpoint_limit(current_bytes, max_bytes)
    return max_bytes


def _checkpoint_limit(free_bytes: int, current_bytes: int) -> int:
    reserved_bytes = max(PUBLIC_ASSET_CHECKPOINT_MIN_FREE_BYTES, free_bytes // 3)
    growth_budget = (free_bytes - reserved_bytes) // 2
    return min(PUBLIC_ASSET_CHECKPOINT_MAX_FILE_BYTES, current_bytes + growth_budget)


def _validate_checkpoint_limit(current_bytes: int, max_bytes: int) -> None:
    if max_bytes <= 0:
        raise RuntimeError(
            "asset checkpoint filesystem has insufficient free space; "
            "at least 8 GiB must remain available"
        )
    if current_bytes > max_bytes:
        raise RuntimeError(
            "asset checkpoint is too large for the safe local-storage limit; "
            "free local space or use the durable checkpoint"
        )


def _checkpoint_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return dict(connection.execute("SELECT key, value FROM checkpoint_metadata").fetchall())


def _checkpoint_metadata_matches(
    metadata: Mapping[str, str],
    input_hashes: Sequence[str],
    polygon_fingerprint: str,
) -> bool:
    return (
        metadata.get("schema_version") == str(PUBLIC_ASSET_DEDUP_CHECKPOINT_SCHEMA_VERSION)
        and json.loads(metadata["input_hashes"]) == list(input_hashes)
        and metadata.get("polygon_fingerprint") == polygon_fingerprint
    )


def _checkpoint_sources_match(connection: sqlite3.Connection, input_hashes: Sequence[str]) -> bool:
    for source_index, source_sha256, row_count, orphan_count in connection.execute(
        "SELECT source_index, source_sha256, row_count, orphan_count FROM checkpoint_sources"
    ):
        if not _checkpoint_source_is_valid(
            source_index, source_sha256, row_count, orphan_count, input_hashes
        ):
            return False
    return True


def _checkpoint_source_is_valid(
    source_index: int,
    source_sha256: str,
    row_count: int,
    orphan_count: int,
    input_hashes: Sequence[str],
) -> bool:
    return (
        0 <= source_index < len(input_hashes)
        and input_hashes[source_index] == source_sha256
        and row_count >= 0
        and 0 <= orphan_count <= row_count
    )


def is_compatible_asset_checkpoint(
    path: Path,
    input_hashes: Sequence[str],
    polygon_fingerprint: str,
) -> bool:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path)
        metadata = _checkpoint_metadata(connection)
        return _checkpoint_metadata_matches(
            metadata, input_hashes, polygon_fingerprint
        ) and _checkpoint_sources_match(connection, input_hashes)
    except (
        OSError,
        sqlite3.DatabaseError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return False
    finally:
        if connection is not None:
            connection.close()


def _remove_incompatible_checkpoint(
    path: Path, input_hashes: Sequence[str] | None, polygon_fingerprint: str
) -> None:
    if (
        input_hashes is not None
        and path.is_file()
        and not is_compatible_asset_checkpoint(path, input_hashes, polygon_fingerprint)
    ):
        remove_checkpoint_files(path)


def _is_external_checkpoint(database_path: Path, checkpoint_root: Path | None) -> bool:
    return (
        checkpoint_root is not None
        and database_path.parent == checkpoint_root.expanduser().resolve()
    )


def _cleanup_public_asset_checkpoints(paths: Sequence[Path], succeeded: bool) -> None:
    if not succeeded:
        return
    for path in paths:
        remove_checkpoint_files(path)


__all__ = [
    "PUBLIC_ASSET_CHECKPOINT_FILENAME",
    "PUBLIC_ASSET_CHECKPOINT_MAX_FILE_BYTES",
    "PUBLIC_ASSET_CHECKPOINT_MIN_FREE_BYTES",
    "PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE",
    "PUBLIC_ASSET_DEDUP_CHECKPOINT_SCHEMA_VERSION",
    "PUBLIC_ASSET_SQLITE_CACHE_KIB",
    "PUBLIC_ASSET_SQLITE_MMAP_BYTES",
    "PUBLIC_ASSET_SQLITE_PAGE_SIZE",
    "is_compatible_asset_checkpoint",
]
