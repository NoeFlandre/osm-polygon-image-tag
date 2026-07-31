"""Aggregation, caching, and map generation for the dataset-card polygon density.

The pipeline is the only public entry point used by the metadata
generator. It:

1. Reads finalized polygon manifests via :func:`verified_manifests`.
2. Decodes per-shard geometry centroids with :mod:`.inputs`.
3. Assigns centroids to H3 cells at :data:`DEFAULT_H3_RESOLUTION` and
   aggregates raw counts per cell.
4. Caches compact per-shard H3 counts under a private
   ``cache/geographic-density/`` directory keyed by finalized manifest
   identity (relative path, SHA-256, row count). The cache is invalidated
   automatically when the manifest identity changes; it is never included
   in the publication inventory.
5. Renders the deterministic static PNG via :mod:`.render`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from osm_polygon_image_tag.artifacts.manifest_inventory import verified_manifests
from osm_polygon_image_tag.core.manifest import Manifest

from .h3 import DEFAULT_H3_RESOLUTION, assign_h3_cell
from .inputs import read_shard_polygon_centroids
from .models import (
    GeographicMapError,
    MapResult,
    MapStatistics,
    PolygonCountCell,
    RenderResult,
)
from .render import GEOGRAPHIC_PNG_RELATIVE, render_count_map

LOGGER = logging.getLogger(__name__)

Progress = Callable[[dict[str, object]], None]

# Private cache directory under the managed data root. The cache is
# never included in the publication inventory.
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


def _cache_root(data_root: Path) -> Path:
    return data_root / CACHE_DIR_RELATIVE


def _atomic_write(path: Path, content: bytes) -> None:
    """Write ``content`` to ``path`` atomically via a sibling temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
    try:
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _shard_cache_path(cache_root: Path) -> Path:
    return cache_root / CACHE_PER_SHARD_FILENAME


def _stats_cache_path(cache_root: Path) -> Path:
    return cache_root / CACHE_STATS_FILENAME


def _compute_input_digest(
    shards: list[tuple[str, str, int]],
) -> str:
    """Compute a deterministic input digest over the per-shard cache keys."""
    payload = json.dumps(shards, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_per_shard_cache(cache_root: Path) -> PerShardCache | None:
    """Load compact per-shard counts, returning ``None`` for bad state."""
    path = _shard_cache_path(cache_root)
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


def _write_per_shard_cache(
    cache_root: Path,
    per_shard: PerShardCache,
) -> None:
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
    _atomic_write(_shard_cache_path(cache_root), serialized)


def _load_stats_cache(cache_root: Path) -> dict[str, object] | None:
    path = _stats_cache_path(cache_root)
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


def _write_stats_cache(
    cache_root: Path,
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
    _atomic_write(_stats_cache_path(cache_root), serialized)


def _aggregate_per_shard(
    manifests: list[tuple[Manifest, Path]],
    data_root: Path,
    *,
    progress: Progress | None,
) -> tuple[
    PerShardCache,
    list[str],
    int,
]:
    """Aggregate per-shard H3 counts. Returns (per_shard, reused_shards, polygon_rows)."""
    emit = progress or (lambda _event: None)
    cache_root = _cache_root(data_root)
    cached = _load_per_shard_cache(cache_root) or {}
    per_shard: PerShardCache = {}
    reused_shards: list[str] = []
    polygon_rows = 0
    for index, (manifest, output) in enumerate(manifests, start=1):
        relative_path = manifest.output.relative_path
        cached_entry = cached.get(relative_path)
        if (
            cached_entry is not None
            and cached_entry["sha256"] == manifest.output.sha256
            and cached_entry["row_count"] == manifest.output.row_count
        ):
            per_shard[relative_path] = cached_entry
            reused_shards.append(relative_path)
            polygon_rows += manifest.output.row_count
            emit(
                {
                    "event": "metadata_geography_shard_reused",
                    "shard": relative_path,
                    "shard_index": index,
                    "shard_count": len(manifests),
                }
            )
            continue
        emit(
            {
                "event": "metadata_geography_shard_started",
                "shard": relative_path,
                "shard_index": index,
                "shard_count": len(manifests),
                "row_count": manifest.output.row_count,
            }
        )
        shard_counts: dict[str, int] = {}
        for centroid in read_shard_polygon_centroids(output, relative_path):
            cell = assign_h3_cell(centroid.lat, centroid.lon, resolution=DEFAULT_H3_RESOLUTION)
            shard_counts[cell] = shard_counts.get(cell, 0) + 1
        if sum(shard_counts.values()) != manifest.output.row_count:
            raise GeographicMapError(
                f"Centroid count {sum(shard_counts.values())} != manifest row count "
                f"{manifest.output.row_count} for {relative_path}"
            )
        per_shard[relative_path] = {
            "sha256": manifest.output.sha256,
            "row_count": manifest.output.row_count,
            "cells": shard_counts,
        }
        polygon_rows += manifest.output.row_count
        emit(
            {
                "event": "metadata_geography_shard_completed",
                "shard": relative_path,
                "shard_index": index,
                "shard_count": len(manifests),
                "row_count": manifest.output.row_count,
            }
        )
    return per_shard, reused_shards, polygon_rows


def _aggregate_cells(
    per_shard: PerShardCache,
) -> list[PolygonCountCell]:
    """Combine per-shard centroid cells into deterministic sorted PolygonCountCells."""
    counts: dict[str, int] = {}
    for entry in per_shard.values():
        for cell, count in entry["cells"].items():
            counts[cell] = counts.get(cell, 0) + count
    return [PolygonCountCell(h3_cell=cell, polygon_count=counts[cell]) for cell in sorted(counts)]


def _input_digest_and_meta(
    manifests: list[tuple[Manifest, Path]],
) -> tuple[str, list[tuple[str, str, int]]]:
    """Compute the deterministic input digest over manifest identities."""
    keys: list[tuple[str, str, int]] = []
    for manifest, _ in manifests:
        keys.append(
            (
                manifest.output.relative_path,
                manifest.output.sha256,
                manifest.output.row_count,
            )
        )
    keys.sort()
    return _compute_input_digest(keys), keys


def build_geographic_map(
    data_root: Path,
    *,
    progress: Progress | None = None,
) -> MapResult:
    """Build, reuse, or refresh the geographic distribution map.

    The function is idempotent: identical finalized inputs always
    produce the same PNG bytes and the same statistics payload. New
    or changed shards cause only the affected shards to be re-decoded.
    The PNG is regenerated when the cache is rebuilt or when the file
    is missing.
    """
    emit = progress or (lambda _event: None)
    emit({"event": "metadata_geography_started"})

    cache_root = _cache_root(data_root)
    manifests = verified_manifests(data_root)
    input_digest, _keys = _input_digest_and_meta(manifests)

    per_shard, reused_shards, polygon_rows = _aggregate_per_shard(
        manifests, data_root, progress=emit
    )

    cells = _aggregate_cells(per_shard)
    cell_count = len(cells)
    if cells:
        min_cell_count = min(cell.polygon_count for cell in cells)
        max_cell_count = max(cell.polygon_count for cell in cells)
    else:
        min_cell_count = 0
        max_cell_count = 0

    cached_row_total = sum(entry["row_count"] for entry in per_shard.values())
    if cached_row_total != polygon_rows:
        raise GeographicMapError(
            f"Per-shard cache row count mismatch: got {cached_row_total}, expected {polygon_rows}"
        )

    png_path = data_root / GEOGRAPHIC_PNG_RELATIVE
    cached_stats = _load_stats_cache(cache_root)

    def cached_int(name: str, expected: int) -> bool:
        value = cached_stats.get(name) if cached_stats is not None else None
        return isinstance(value, int) and not isinstance(value, bool) and value == expected

    cache_is_valid = (
        cached_stats is not None
        and cached_stats.get("input_digest") == input_digest
        and cached_int("input_shard_count", len(manifests))
        and cached_int("cell_count", cell_count)
        and cached_int("polygon_rows", polygon_rows)
        and cached_int("min_cell_count", min_cell_count)
        and cached_int("max_cell_count", max_cell_count)
        and png_path.is_file()
        and png_path.stat().st_size > 0
        # Only trust the PNG signature when the file is structurally a PNG.
        and png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    )

    if cache_is_valid:
        # The PNG is already valid; render_count_map is intentionally not
        # called here because a cache hit must avoid Matplotlib work. Keep a
        # deterministic caption for callers inspecting the result.
        caption = (
            "Geographic OSM Polygon Density. Each H3 cell contains the raw count of "
            "finalized `polygons` rows whose geometry centroid falls into the cell. "
            f"Polygons are assigned by their geometry centroid at H3 resolution "
            f"{DEFAULT_H3_RESOLUTION}; overlapping Geofabrik extracts are preserved "
            f"as separate observations. {polygon_rows:,} finalized polygon rows "
            f"across {cell_count:,} H3 cells. `image_assets` rows are not separately "
            "counted in this map. Colour uses a logarithmic scale."
        )
        render = RenderResult(output_path=png_path, caption=caption)
    else:
        render = render_count_map(cells, png_path, h3_resolution=DEFAULT_H3_RESOLUTION)

    # Persist caches for the next run.
    _write_per_shard_cache(cache_root, per_shard)
    _write_stats_cache(
        cache_root,
        input_digest=input_digest,
        input_shard_count=len(manifests),
        h3_resolution=DEFAULT_H3_RESOLUTION,
        cell_count=cell_count,
        polygon_rows=polygon_rows,
        min_cell_count=min_cell_count,
        max_cell_count=max_cell_count,
    )

    statistics = MapStatistics(
        h3_resolution=DEFAULT_H3_RESOLUTION,
        cell_count=cell_count,
        polygon_rows=polygon_rows,
        min_cell_count=min_cell_count,
        max_cell_count=max_cell_count,
        input_shard_count=len(manifests),
        input_digest=input_digest,
    )

    emit(
        {
            "event": "metadata_geography_completed",
            "h3_resolution": statistics.h3_resolution,
            "cell_count": statistics.cell_count,
            "polygon_rows": statistics.polygon_rows,
            "min_cell_count": statistics.min_cell_count,
            "max_cell_count": statistics.max_cell_count,
            "input_shard_count": statistics.input_shard_count,
            "reused_shard_count": len(reused_shards),
            "rebuilt_shard_count": len(manifests) - len(reused_shards),
        }
    )

    return MapResult(cells=tuple(cells), statistics=statistics, render=render)


__all__ = [
    "CACHE_DIR_RELATIVE",
    "CACHE_PER_SHARD_FILENAME",
    "CACHE_SCHEMA_VERSION",
    "CACHE_STATS_FILENAME",
    "GEOGRAPHIC_PNG_RELATIVE",
    "build_geographic_map",
]
