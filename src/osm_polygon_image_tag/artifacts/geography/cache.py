"""Private, deterministic persistence for the geographic-density cache."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import TypedDict

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
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
    try:
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _shard_cache_path(cache_dir: Path) -> Path:
    return cache_dir / CACHE_PER_SHARD_FILENAME


def _stats_cache_path(cache_dir: Path) -> Path:
    return cache_dir / CACHE_STATS_FILENAME


def input_digest(shards: list[tuple[str, str, int]]) -> str:
    """Compute a deterministic digest over finalized shard identities."""
    payload = json.dumps(sorted(shards), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
        return None
    if payload.get("h3_resolution") != DEFAULT_H3_RESOLUTION:
        return None
    shards = payload.get("shards")
    if not isinstance(shards, dict):
        return None
    result: PerShardCache = {}
    for shard_path, payload_entry in shards.items():
        if not isinstance(shard_path, str) or not isinstance(payload_entry, dict):
            return None
        sha256 = payload_entry.get("sha256")
        row_count = payload_entry.get("row_count")
        cells = payload_entry.get("cells")
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
            or not isinstance(row_count, int)
            or isinstance(row_count, bool)
            or row_count < 0
            or not isinstance(cells, list)
        ):
            return None
        parsed: dict[str, int] = {}
        for cell_entry in cells:
            if not isinstance(cell_entry, dict):
                return None
            cell = cell_entry.get("h3_cell")
            count = cell_entry.get("polygon_count")
            if (
                not isinstance(cell, str)
                or not cell
                or cell in parsed
                or not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
            ):
                return None
            parsed[cell] = count
        if sum(parsed.values()) != row_count:
            return None
        result[shard_path] = {
            "sha256": sha256,
            "row_count": row_count,
            "cells": parsed,
        }
    return result


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
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
        return None
    if payload.get("h3_resolution") != DEFAULT_H3_RESOLUTION:
        return None
    return payload


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
