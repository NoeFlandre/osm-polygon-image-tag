import sqlite3
from pathlib import Path

import pyarrow.parquet as pq

from osm_polygon_image_tag.manifest import (
    DATASET_SCHEMA_VERSION,
    PROCESSING_CONTRACT_VERSION,
    Manifest,
    file_sha256,
    read_manifest,
)
from osm_polygon_image_tag.storage import validate_geoparquet

PROVIDERS = (
    "image",
    "wikimedia_commons",
    "mapillary",
    "panoramax",
    "kartaview",
    "flickr",
    "bubbleid",
)


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS shards (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS observations (
            shard TEXT NOT NULL,
            osm_type TEXT NOT NULL,
            osm_id INTEGER NOT NULL,
            osm_version INTEGER,
            geometry_type TEXT NOT NULL,
            area_m2 REAL NOT NULL,
            timestamp TEXT,
            provider_mask INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS identity_idx ON observations (osm_type, osm_id, osm_version)"
    )
    return connection


def verified_manifests(data_root: Path) -> list[tuple[Manifest, Path]]:
    verified: list[tuple[Manifest, Path]] = []
    for manifest_path in sorted((data_root / "manifests").glob("*.manifest.json")):
        manifest = read_manifest(manifest_path)
        if (
            manifest.processing_contract_version != PROCESSING_CONTRACT_VERSION
            or manifest.dataset_schema_version != DATASET_SCHEMA_VERSION
        ):
            continue
        output = (data_root / manifest.output.relative_path).resolve()
        if data_root.resolve() not in output.parents:
            raise ValueError(f"output escapes data root: {output}")
        if (
            output.stat().st_size != manifest.output.size_bytes
            or file_sha256(output) != manifest.output.sha256
        ):
            raise ValueError(f"output identity mismatch: {output}")
        validate_geoparquet(output)
        verified.append((manifest, output))
    return verified


def sync_catalog(data_root: Path, *, batch_size: int = 8192) -> Path:
    catalog_path = data_root / "catalog" / "catalog.sqlite"
    manifests = verified_manifests(data_root)
    active = {manifest.output.relative_path: manifest.output.sha256 for manifest, _ in manifests}
    with _connect(catalog_path) as connection:
        existing = dict(connection.execute("SELECT path, sha256 FROM shards"))
        for stale in sorted(set(existing) - set(active)):
            connection.execute("DELETE FROM observations WHERE shard = ?", (stale,))
            connection.execute("DELETE FROM shards WHERE path = ?", (stale,))
        for manifest, output in manifests:
            shard = manifest.output.relative_path
            if existing.get(shard) == manifest.output.sha256:
                continue
            connection.execute("DELETE FROM observations WHERE shard = ?", (shard,))
            inserted = 0
            parquet = pq.ParquetFile(output)
            columns = [
                "osm_type",
                "osm_id",
                "osm_version",
                "geometry_type",
                "area_m2",
                "osm_timestamp",
                *PROVIDERS,
                "panoramax_values",
            ]
            for batch in parquet.iter_batches(batch_size=batch_size, columns=columns):
                values = []
                for row in batch.to_pylist():
                    mask = sum(
                        1 << index
                        for index, provider in enumerate(PROVIDERS)
                        if (
                            bool(row["panoramax_values"])
                            if provider == "panoramax"
                            else row[provider] is not None
                        )
                    )
                    timestamp = row["osm_timestamp"]
                    values.append(
                        (
                            shard,
                            row["osm_type"],
                            row["osm_id"],
                            row["osm_version"],
                            row["geometry_type"],
                            row["area_m2"],
                            timestamp.isoformat() if timestamp is not None else None,
                            mask,
                        )
                    )
                connection.executemany(
                    "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
                inserted += len(values)
            if inserted != manifest.output.row_count:
                raise ValueError(f"catalog row mismatch for {shard}")
            connection.execute(
                "INSERT OR REPLACE INTO shards (path, sha256) VALUES (?, ?)",
                (shard, manifest.output.sha256),
            )
    return catalog_path
