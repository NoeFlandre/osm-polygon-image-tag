import json
import os
import sqlite3
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from osm_polygon_image_tag.artifacts.catalog import PROVIDERS, sync_catalog, verified_manifests
from osm_polygon_image_tag.core.manifest import Manifest
from osm_polygon_image_tag.core.progress import Progress


@dataclass(frozen=True, slots=True)
class MetadataResult:
    statistics_path: Path
    card_path: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "statistics_path": str(self.statistics_path),
            "card_path": str(self.card_path),
        }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
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


def _statistics(catalog_path: Path, manifests: list[tuple[Manifest, Path]]) -> dict[str, Any]:
    rejections: Counter[str] = Counter()
    for manifest, _output in manifests:
        rejections.update(manifest.counts.rejections)
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
        provider_counts = {
            provider: int(
                connection.execute(
                    "SELECT COUNT(*) FROM observations WHERE (provider_mask & ?) != 0",
                    (1 << index,),
                ).fetchone()[0]
            )
            for index, provider in enumerate(PROVIDERS)
        }
        combinations = {
            "+".join(
                provider for index, provider in enumerate(PROVIDERS) if int(mask) & (1 << index)
            ): int(count)
            for mask, count in connection.execute(
                "SELECT provider_mask, COUNT(*) FROM observations "
                "GROUP BY provider_mask ORDER BY provider_mask"
            )
        }
        return {
            "schema_version": 1,
            "shards": len(manifests),
            "rows": rows,
            "source_bytes": sum(manifest.source.size_bytes for manifest, _ in manifests),
            "output_bytes": sum(manifest.output.size_bytes for manifest, _ in manifests),
            "osm_types": _pairs(connection, "osm_type"),
            "geometry_types": _pairs(connection, "geometry_type"),
            "provider_counts": provider_counts,
            "provider_combinations": combinations,
            "timestamp_min": timestamp[0],
            "timestamp_max": timestamp[1],
            "area_m2": {
                "sum": area[0],
                "min": area[1],
                "max": area[2],
                "mean": area[3],
            },
            "rejections": dict(sorted(rejections.items())),
            "duplicate_observations": int(duplicate),
            "shard_digests": {
                manifest.output.relative_path: manifest.output.sha256 for manifest, _ in manifests
            },
        }


def _card(statistics: dict[str, Any]) -> bytes:
    providers = "\n".join(
        f"- `{provider}`: {count}" for provider, count in statistics["provider_counts"].items()
    )
    text = f"""---
license: odbl
tags:
- openstreetmap
- geospatial
- geoparquet
- image
---
# OSM Polygon Image Tag

This dataset contains OpenStreetMap Polygon and MultiPolygon observations whose
way or relation carries at least one raw image-reference tag.

## Current verified contents

- Shards: {statistics["shards"]}
- Rows: {statistics["rows"]}
- Duplicate observations across source PBFs: {statistics["duplicate_observations"]}

Provider observations:
{providers}

## Schema

Rows include OSM type/ID/version/changeset/timestamp, source PBF identity, full
OGC:CRS84 WKB geometry, geodesic `area_m2`, bounds, every original OSM tag, and
the exact raw `image`, `wikimedia_commons`, `mapillary`, `panoramax`,
`panoramax_values`, `kartaview`, `flickr`, and `bubbleid` values.

## Provenance and license

Source extracts are provided by Geofabrik from OpenStreetMap. OpenStreetMap data
is available under the Open Database License. Attribution: © OpenStreetMap
contributors.

## Limitations and intended use

References may be stale, inaccessible, provider-specific, or unrelated to the
current feature. Inclusion does not establish image copyright, licensing,
safety, availability, or correspondence to the mapped feature. No provider API
is called and no image is downloaded or validated.

Overlapping Geofabrik extracts are intentionally preserved as separate
observations and quantified above. Statistics in this card are generated only
from cryptographically verified manifests and GeoParquet shards.
"""
    return text.encode("utf-8")


def generate_metadata(data_root: Path, *, progress: Progress | None = None) -> MetadataResult:
    emit = progress or (lambda _event: None)
    manifests = verified_manifests(data_root, progress=emit)
    catalog_path = sync_catalog(data_root, manifests=manifests, progress=emit)
    emit({"event": "metadata_statistics_started"})
    statistics = _statistics(catalog_path, manifests)
    emit(
        {
            "event": "metadata_statistics_completed",
            "shards": statistics["shards"],
            "rows": statistics["rows"],
        }
    )
    statistics_path = data_root / "statistics" / "dataset-statistics.json"
    card_path = data_root / "README.md"
    serialized = (
        json.dumps(statistics, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")
    emit({"event": "metadata_write_started"})
    _atomic_write(statistics_path, serialized)
    _atomic_write(card_path, _card(statistics))
    emit(
        {
            "event": "metadata_write_completed",
            "statistics_path": str(statistics_path),
            "card_path": str(card_path),
        }
    )
    return MetadataResult(statistics_path=statistics_path, card_path=card_path)
