"""Aggregation and map generation for the dataset-card polygon density.

The pipeline is the only public entry point used by the metadata
generator. It:

1. Reads finalized polygon manifests via :func:`verified_manifests`.
2. Decodes per-shard geometry centroids with :mod:`.inputs`.
3. Assigns centroids to H3 cells at :data:`DEFAULT_H3_RESOLUTION` and
   aggregates raw counts per cell.
4. Reuses the private, identity-keyed per-shard H3 cache owned by :mod:`.cache`.
5. Renders the deterministic static PNG via :mod:`.render`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from osm_polygon_image_tag.artifacts.manifest_inventory import verified_manifests
from osm_polygon_image_tag.core.manifest import Manifest

from .cache import (
    CACHE_DIR_RELATIVE,
    CACHE_PER_SHARD_FILENAME,
    CACHE_SCHEMA_VERSION,
    CACHE_STATS_FILENAME,
    PerShardCache,
    ShardCacheEntry,
    cache_root,
    input_digest,
    load_shard_cache,
    load_stats_cache,
    write_shard_cache,
    write_stats_cache,
)
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

Progress = Callable[[dict[str, object]], None]


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
    cache_dir = cache_root(data_root)
    cached = load_shard_cache(cache_dir) or {}
    per_shard: PerShardCache = {}
    reused_shards: list[str] = []
    polygon_rows = 0
    for index, (manifest, output) in enumerate(manifests, start=1):
        relative_path = manifest.output.relative_path
        cached_entry = cached.get(relative_path)
        reusable = _reusable_shard(cached_entry, manifest)
        if reusable is not None:
            per_shard[relative_path] = reusable
            reused_shards.append(relative_path)
            polygon_rows += manifest.output.row_count
            emit(_shard_event("metadata_geography_shard_reused", relative_path, index, manifests))
            continue
        emit(
            _shard_event(
                "metadata_geography_shard_started", relative_path, index, manifests, manifest
            )
        )
        per_shard[relative_path] = _aggregate_shard(manifest, output, relative_path)
        polygon_rows += manifest.output.row_count
        emit(
            _shard_event(
                "metadata_geography_shard_completed", relative_path, index, manifests, manifest
            )
        )
    return per_shard, reused_shards, polygon_rows


def _reusable_shard(entry: ShardCacheEntry | None, manifest: Manifest) -> ShardCacheEntry | None:
    if (
        entry is not None
        and entry["sha256"] == manifest.output.sha256
        and entry["row_count"] == manifest.output.row_count
    ):
        return entry
    return None


def _shard_event(
    event: str,
    relative_path: str,
    index: int,
    manifests: list[tuple[Manifest, Path]],
    manifest: Manifest | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "event": event,
        "shard": relative_path,
        "shard_index": index,
        "shard_count": len(manifests),
    }
    if manifest is not None:
        payload["row_count"] = manifest.output.row_count
    return payload


def _aggregate_shard(manifest: Manifest, output: Path, relative_path: str) -> ShardCacheEntry:
    shard_counts: dict[str, int] = {}
    for centroid in read_shard_polygon_centroids(output, relative_path):
        cell = assign_h3_cell(centroid.lat, centroid.lon, resolution=DEFAULT_H3_RESOLUTION)
        shard_counts[cell] = shard_counts.get(cell, 0) + 1
    if sum(shard_counts.values()) != manifest.output.row_count:
        raise GeographicMapError(
            f"Centroid count {sum(shard_counts.values())} != manifest row count "
            f"{manifest.output.row_count} for {relative_path}"
        )
    return {
        "sha256": manifest.output.sha256,
        "row_count": manifest.output.row_count,
        "cells": shard_counts,
    }


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
    return input_digest(keys), keys


def _cached_int(cached_stats: dict[str, object] | None, name: str, expected: int) -> bool:
    value = cached_stats.get(name) if cached_stats is not None else None
    return isinstance(value, int) and not isinstance(value, bool) and value == expected


def _map_cache_is_valid(
    cached_stats: dict[str, object] | None,
    input_digest_value: str,
    input_shard_count: int,
    cell_count: int,
    polygon_rows: int,
    min_cell_count: int,
    max_cell_count: int,
    png_path: Path,
) -> bool:
    return (
        _cache_identity_matches(cached_stats, input_digest_value, input_shard_count)
        and _cache_dimensions_match(
            cached_stats, cell_count, polygon_rows, min_cell_count, max_cell_count
        )
        and _png_is_valid(png_path)
    )


def _cache_identity_matches(
    cached_stats: dict[str, object] | None, input_digest_value: str, input_shard_count: int
) -> bool:
    return (
        cached_stats is not None
        and cached_stats.get("input_digest") == input_digest_value
        and _cached_int(cached_stats, "input_shard_count", input_shard_count)
    )


def _cache_dimensions_match(
    cached_stats: dict[str, object] | None,
    cell_count: int,
    polygon_rows: int,
    min_cell_count: int,
    max_cell_count: int,
) -> bool:
    return cached_stats is not None and all(
        _cached_int(cached_stats, name, expected)
        for name, expected in (
            ("cell_count", cell_count),
            ("polygon_rows", polygon_rows),
            ("min_cell_count", min_cell_count),
            ("max_cell_count", max_cell_count),
        )
    )


def _png_is_valid(path: Path) -> bool:
    return (
        path.is_file() and path.stat().st_size > 0 and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    )


def _cell_count_range(cells: list[PolygonCountCell]) -> tuple[int, int]:
    if not cells:
        return 0, 0
    counts = [cell.polygon_count for cell in cells]
    return min(counts), max(counts)


def _cached_render(png_path: Path, polygon_rows: int, cell_count: int) -> RenderResult:
    caption = (
        "Geographic OSM Polygon Density. Each H3 cell contains the raw count of "
        "finalized `polygons` rows whose geometry centroid falls into the cell. "
        f"Polygons are assigned by their geometry centroid at H3 resolution "
        f"{DEFAULT_H3_RESOLUTION}. {polygon_rows:,} supplied polygon rows "
        f"across {cell_count:,} H3 cells. Image rows and polygon-image links "
        "are not counted in this map. Colour uses a logarithmic scale."
    )
    return RenderResult(output_path=png_path, caption=caption)


def build_geographic_map(
    data_root: Path,
    *,
    manifests: Sequence[tuple[Manifest, Path]] | None = None,
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

    cache_dir = cache_root(data_root)
    selected_manifests = list(manifests) if manifests is not None else verified_manifests(data_root)
    input_digest, _keys = _input_digest_and_meta(selected_manifests)

    per_shard, reused_shards, polygon_rows = _aggregate_per_shard(
        selected_manifests, data_root, progress=emit
    )

    cells = _aggregate_cells(per_shard)
    cell_count = len(cells)
    min_cell_count, max_cell_count = _cell_count_range(cells)

    cached_row_total = sum(entry["row_count"] for entry in per_shard.values())
    _validate_cached_row_total(cached_row_total, polygon_rows)

    png_path = data_root / GEOGRAPHIC_PNG_RELATIVE
    cached_stats = load_stats_cache(cache_dir)
    render = (
        _cached_render(png_path, polygon_rows, cell_count)
        if _map_cache_is_valid(
            cached_stats,
            input_digest,
            len(selected_manifests),
            cell_count,
            polygon_rows,
            min_cell_count,
            max_cell_count,
            png_path,
        )
        else render_count_map(cells, png_path, h3_resolution=DEFAULT_H3_RESOLUTION)
    )

    # Persist caches for the next run.
    write_shard_cache(cache_dir, per_shard)
    write_stats_cache(
        cache_dir,
        input_digest=input_digest,
        input_shard_count=len(selected_manifests),
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
        input_shard_count=len(selected_manifests),
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
            "rebuilt_shard_count": len(selected_manifests) - len(reused_shards),
        }
    )

    return MapResult(cells=tuple(cells), statistics=statistics, render=render)


def _validate_cached_row_total(cached_rows: int, polygon_rows: int) -> None:
    if cached_rows != polygon_rows:
        raise GeographicMapError(
            f"Per-shard cache row count mismatch: got {cached_rows}, expected {polygon_rows}"
        )


__all__ = [
    "CACHE_DIR_RELATIVE",
    "CACHE_PER_SHARD_FILENAME",
    "CACHE_SCHEMA_VERSION",
    "CACHE_STATS_FILENAME",
    "GEOGRAPHIC_PNG_RELATIVE",
    "build_geographic_map",
]
