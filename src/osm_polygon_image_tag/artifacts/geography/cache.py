"""Private, deterministic persistence for the geographic-density cache."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypedDict, cast

from osm_polygon_image_tag.core.atomic import atomic_write_bytes

from .h3 import DEFAULT_H3_RESOLUTION

LOGGER = logging.getLogger(__name__)

CACHE_DIR_RELATIVE: str = "cache/geographic-density"
CACHE_SCHEMA_VERSION: int = 2
CACHE_STATS_FILENAME: str = "pipeline.json"
CACHE_PER_SHARD_FILENAME: str = "shards.json"


class ShardCacheEntry(TypedDict):
    """Compact, identity-bound H3 counts for one finalized shard."""

    sha256: str
    row_count: int
    cells: dict[str, int]


PerShardCache = dict[str, ShardCacheEntry]


def cache_root(data_root: Path) -> Path:
    return data_root / CACHE_DIR_RELATIVE


def _atomic_write(path: Path, content: bytes) -> None:
    """Write bytes atomically and remove the temporary sibling on failure."""
    atomic_write_bytes(path, content, prefix=f".{path.name}.", suffix=".tmp")


def _shard_cache_path(cache_dir: Path) -> Path:
    return cache_dir / CACHE_PER_SHARD_FILENAME


def _stats_cache_path(cache_dir: Path) -> Path:
    return cache_dir / CACHE_STATS_FILENAME


def input_digest(shards: list[tuple[str, str, int]]) -> str:
    """Compute a deterministic digest over finalized shard identities."""
    payload = json.dumps(sorted(shards), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _parse_cell_entry(value: object) -> tuple[str, int] | None:
    if not isinstance(value, dict):
        return None
    cell = value.get("h3_cell")
    count = _nonnegative_int(value.get("polygon_count"))
    if not isinstance(cell, str) or not cell or count is None:
        return None
    return cell, count


def _parse_cells(value: object, *, row_count: int) -> dict[str, int] | None:
    if not isinstance(value, list):
        return None
    parsed = _parse_cell_entries(value)
    if parsed is None:
        return None
    return _matching_cell_counts(parsed, row_count)


def _matching_cell_counts(parsed: dict[str, int], row_count: int) -> dict[str, int] | None:
    return parsed if sum(parsed.values()) == row_count else None


def _parse_cell_entries(value: Sequence[object]) -> dict[str, int] | None:
    parsed: dict[str, int] = {}
    for cell_entry in value:
        parsed_entry = _parse_cell_entry(cell_entry)
        if parsed_entry is None:
            return None
        cell, count = parsed_entry
        if cell in parsed:
            return None
        parsed[cell] = count
    return parsed


def _parse_shard_entry(value: object) -> ShardCacheEntry | None:
    if not isinstance(value, dict):
        return None
    return _build_shard_entry(cast(Mapping[str, object], value))


def _build_shard_entry(value: Mapping[str, object]) -> ShardCacheEntry | None:
    identity = _shard_identity(value)
    if identity is None:
        return None
    sha256, row_count_value = identity
    cells = _parse_cells(value.get("cells"), row_count=row_count_value)
    if cells is None:
        return None
    return {"sha256": sha256, "row_count": row_count_value, "cells": cells}


def _shard_identity(value: Mapping[str, object]) -> tuple[str, int] | None:
    sha256 = value.get("sha256")
    row_count = _nonnegative_int(value.get("row_count"))
    if not _valid_sha256(sha256) or row_count is None:
        return None
    return cast(str, sha256), row_count


def _parse_shards(value: object) -> PerShardCache | None:
    if not isinstance(value, dict):
        return None
    result: PerShardCache = {}
    for shard_path, payload_entry in value.items():
        if not _valid_shard_path(shard_path):
            return None
        entry = _parse_shard_entry(payload_entry)
        if entry is None:
            return None
        result[cast(str, shard_path)] = entry
    return result


def _valid_shard_path(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _parse_cache_payload(value: object) -> PerShardCache | None:
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") != CACHE_SCHEMA_VERSION:
        return None
    if value.get("h3_resolution") != DEFAULT_H3_RESOLUTION:
        return None
    return _parse_shards(value.get("shards"))


def load_shard_cache(cache_dir: Path) -> PerShardCache | None:
    """Load validated per-shard counts, returning ``None`` for bad state."""
    path = _shard_cache_path(cache_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        LOGGER.warning("Geographic density cache is unreadable; rebuilding")
        return None
    return _parse_cache_payload(payload)


def write_shard_cache(cache_dir: Path, per_shard: PerShardCache) -> None:
    """Atomically write compact, deterministic per-shard H3 counts."""
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "h3_resolution": DEFAULT_H3_RESOLUTION,
        "shards": {
            shard: {
                "sha256": entry["sha256"],
                "row_count": entry["row_count"],
                "cells": [
                    {"h3_cell": cell, "polygon_count": count}
                    for cell, count in sorted(entry["cells"].items())
                ],
            }
            for shard, entry in sorted(per_shard.items())
        },
    }
    serialized = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")
    _atomic_write(_shard_cache_path(cache_dir), serialized)


def load_stats_cache(cache_dir: Path) -> dict[str, object] | None:
    path = _stats_cache_path(cache_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return _valid_stats_payload(payload)


def _valid_stats_payload(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") != CACHE_SCHEMA_VERSION:
        return None
    if value.get("h3_resolution") != DEFAULT_H3_RESOLUTION:
        return None
    return cast(dict[str, object], value)


def write_stats_cache(
    cache_dir: Path,
    *,
    input_digest: str,
    input_shard_count: int,
    h3_resolution: int,
    cell_count: int,
    polygon_rows: int,
    min_cell_count: int,
    max_cell_count: int,
) -> None:
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "input_digest": input_digest,
        "input_shard_count": input_shard_count,
        "h3_resolution": h3_resolution,
        "cell_count": cell_count,
        "polygon_rows": polygon_rows,
        "min_cell_count": min_cell_count,
        "max_cell_count": max_cell_count,
    }
    serialized = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")
    _atomic_write(_stats_cache_path(cache_dir), serialized)


__all__ = [
    "CACHE_DIR_RELATIVE",
    "CACHE_PER_SHARD_FILENAME",
    "CACHE_SCHEMA_VERSION",
    "CACHE_STATS_FILENAME",
    "PerShardCache",
    "ShardCacheEntry",
    "cache_root",
    "input_digest",
    "load_shard_cache",
    "load_stats_cache",
    "write_shard_cache",
    "write_stats_cache",
]
