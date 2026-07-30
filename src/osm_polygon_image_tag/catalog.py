import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pyarrow.parquet as pq

from osm_polygon_image_tag.manifest import (
    DATASET_SCHEMA_VERSION,
    PROCESSING_CONTRACT_VERSION,
    Manifest,
    file_sha256,
    read_manifest,
)
from osm_polygon_image_tag.progress import Progress
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


def verified_manifests(
    data_root: Path, *, progress: Progress | None = None
) -> list[tuple[Manifest, Path]]:
    emit = progress or (lambda _event: None)
    manifest_paths = sorted((data_root / "manifests").glob("*.manifest.json"))
    emit(
        {
            "event": "metadata_manifest_scan_started",
            "manifest_count": len(manifest_paths),
        }
    )
    verified: list[tuple[Manifest, Path]] = []
    skipped = 0
    verified_bytes = 0
    for index, manifest_path in enumerate(manifest_paths, start=1):
        manifest = read_manifest(manifest_path)
        if (
            manifest.processing_contract_version != PROCESSING_CONTRACT_VERSION
            or manifest.dataset_schema_version != DATASET_SCHEMA_VERSION
        ):
            skipped += 1
            if index % 10 == 0 or index == len(manifest_paths):
                emit(
                    {
                        "event": "metadata_manifest_scan_progress",
                        "manifest_count": len(manifest_paths),
                        "manifest_index": index,
                        "verified_shards": len(verified),
                        "skipped_incompatible": skipped,
                        "verified_bytes": verified_bytes,
                    }
                )
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
        verified_bytes += manifest.output.size_bytes
        if index % 10 == 0 or index == len(manifest_paths):
            emit(
                {
                    "event": "metadata_manifest_scan_progress",
                    "manifest_count": len(manifest_paths),
                    "manifest_index": index,
                    "verified_shards": len(verified),
                    "skipped_incompatible": skipped,
                    "verified_bytes": verified_bytes,
                }
            )
    emit(
        {
            "event": "metadata_manifest_scan_completed",
            "manifest_count": len(manifest_paths),
            "verified_shards": len(verified),
            "skipped_incompatible": skipped,
            "verified_bytes": verified_bytes,
        }
    )
    return verified


def sync_catalog(
    data_root: Path,
    *,
    manifests: Sequence[tuple[Manifest, Path]] | None = None,
    batch_size: int = 8192,
    progress: Progress | None = None,
) -> Path:
    emit = progress or (lambda _event: None)
    catalog_path = data_root / "catalog" / "catalog.sqlite"
    selected = list(manifests) if manifests is not None else verified_manifests(data_root)
    emit({"event": "metadata_catalog_sync_started", "active_shards": len(selected)})
    active = {manifest.output.relative_path: manifest.output.sha256 for manifest, _ in selected}
    reused = 0
    indexed = 0
    indexed_rows = 0
    with _connect(catalog_path) as connection:
        existing = dict(connection.execute("SELECT path, sha256 FROM shards"))
        stale_shards = sorted(set(existing) - set(active))
        for stale in stale_shards:
            connection.execute("DELETE FROM observations WHERE shard = ?", (stale,))
            connection.execute("DELETE FROM shards WHERE path = ?", (stale,))
        for shard_index, (manifest, output) in enumerate(selected, start=1):
            shard = manifest.output.relative_path
            if existing.get(shard) == manifest.output.sha256:
                reused += 1
                continue
            emit(
                {
                    "event": "metadata_catalog_shard_started",
                    "shard": shard,
                    "shard_index": shard_index,
                    "shard_count": len(selected),
                    "row_count": manifest.output.row_count,
                }
            )
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
            indexed += 1
            indexed_rows += inserted
            emit(
                {
                    "event": "metadata_catalog_shard_completed",
                    "shard": shard,
                    "shard_index": shard_index,
                    "shard_count": len(selected),
                    "row_count": inserted,
                }
            )
    emit(
        {
            "event": "metadata_catalog_sync_completed",
            "active_shards": len(selected),
            "reused_shards": reused,
            "indexed_shards": indexed,
            "indexed_rows": indexed_rows,
            "removed_stale_shards": len(stale_shards),
        }
    )
    return catalog_path
