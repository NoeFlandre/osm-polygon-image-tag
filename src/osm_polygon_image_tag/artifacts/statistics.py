"""Deterministic aggregate statistics derived from the local catalog."""

import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from osm_polygon_image_tag.artifacts.catalog import PROVIDERS
from osm_polygon_image_tag.core.manifest import Manifest


def _pairs(connection: sqlite3.Connection, column: str) -> dict[str, int]:
    queries = {
        "osm_type": (
            "SELECT osm_type, COUNT(*) FROM observations GROUP BY osm_type ORDER BY osm_type"
        ),
        "geometry_type": (
            "SELECT geometry_type, COUNT(*) FROM observations "
            "GROUP BY geometry_type ORDER BY geometry_type"
        ),
    }
    return {str(key): int(value) for key, value in connection.execute(queries[column])}


def dataset_statistics(
    catalog_path: Path, manifests: list[tuple[Manifest, Path]]
) -> dict[str, Any]:
    """Compute the complete public statistics payload."""
    rejections = _rejection_counts(manifests)
    with sqlite3.connect(catalog_path) as connection:
        rows = int(connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
        area = connection.execute(
            "SELECT SUM(area_m2), MIN(area_m2), MAX(area_m2), AVG(area_m2) FROM observations"
        ).fetchone()
        timestamp = connection.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM observations WHERE timestamp IS NOT NULL"
        ).fetchone()
        duplicate = connection.execute(
            """
            SELECT COALESCE(SUM(count - 1), 0) FROM (
                SELECT COUNT(*) AS count FROM observations
                GROUP BY osm_type, osm_id, osm_version
            )
            """
        ).fetchone()[0]
        provider_counts = _provider_counts(connection)
        combinations = _provider_combinations(connection)
        osm_types = _pairs(connection, "osm_type")
        geometry_types = _pairs(connection, "geometry_type")
    source_bytes, output_bytes = _manifest_sizes(manifests)
    return {
        "schema_version": 1,
        "shards": len(manifests),
        "rows": rows,
        "source_bytes": source_bytes,
        "output_bytes": output_bytes,
        "osm_types": osm_types,
        "geometry_types": geometry_types,
        "provider_counts": provider_counts,
        "provider_combinations": combinations,
        "timestamp_min": timestamp[0],
        "timestamp_max": timestamp[1],
        "area_m2": {"sum": area[0], "min": area[1], "max": area[2], "mean": area[3]},
        "rejections": dict(sorted(rejections.items())),
        "duplicate_observations": int(duplicate),
        "shard_digests": _manifest_digests(manifests),
    }


def _rejection_counts(manifests: list[tuple[Manifest, Path]]) -> Counter[str]:
    rejections: Counter[str] = Counter()
    for manifest, _output in manifests:
        rejections.update(manifest.counts.rejections)
    return rejections


def _provider_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        provider: int(
            connection.execute(
                "SELECT COUNT(*) FROM observations WHERE (provider_mask & ?) != 0",
                (1 << index,),
            ).fetchone()[0]
        )
        for index, provider in enumerate(PROVIDERS)
    }


def _provider_combinations(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        _providers_for_mask(mask): int(count)
        for mask, count in connection.execute(
            "SELECT provider_mask, COUNT(*) FROM observations "
            "GROUP BY provider_mask ORDER BY provider_mask"
        )
    }


def _providers_for_mask(mask: int) -> str:
    return "+".join(
        provider for index, provider in enumerate(PROVIDERS) if int(mask) & (1 << index)
    )


def _manifest_sizes(manifests: list[tuple[Manifest, Path]]) -> tuple[int, int]:
    return (
        sum(manifest.source.size_bytes for manifest, _ in manifests),
        sum(manifest.output.size_bytes for manifest, _ in manifests),
    )


def _manifest_digests(manifests: list[tuple[Manifest, Path]]) -> dict[str, str]:
    return {manifest.output.relative_path: manifest.output.sha256 for manifest, _ in manifests}
