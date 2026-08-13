import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pyarrow.parquet as pq

from osm_polygon_image_tag.assets.manifest import AssetManifest
from osm_polygon_image_tag.core.progress import Progress


def _prepare(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS asset_shards (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_observations (
            shard TEXT NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            canonical_reference TEXT NOT NULL,
            provider_asset_id TEXT,
            image_url TEXT,
            page_url TEXT,
            expires_at TEXT,
            license_id TEXT,
            category_truncated INTEGER NOT NULL,
            retry_after TEXT,
            resolver_contract_version INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS asset_observations_shard_idx ON asset_observations (shard)"
    )


def sync_asset_catalog(
    catalog_path: Path,
    manifests: Sequence[tuple[AssetManifest, Path]],
    *,
    batch_size: int = 8192,
    progress: Progress | None = None,
) -> None:
    emit = progress or (lambda _event: None)
    emit({"event": "metadata_asset_catalog_sync_started", "active_shards": len(manifests)})
    active = {manifest.output.relative_path: manifest.output.sha256 for manifest, _ in manifests}
    reused = indexed = indexed_rows = 0
    with sqlite3.connect(catalog_path) as connection:
        _prepare(connection)
        existing = dict(connection.execute("SELECT path, sha256 FROM asset_shards"))
        stale = sorted(set(existing) - set(active))
        for shard in stale:
            connection.execute("DELETE FROM asset_observations WHERE shard = ?", (shard,))
            connection.execute("DELETE FROM asset_shards WHERE path = ?", (shard,))
        columns = [
            "provider",
            "status",
            "canonical_reference",
            "provider_asset_id",
            "image_url",
            "page_url",
            "image_url_expires_at",
            "license_id",
            "category_truncated",
            "retry_after",
            "resolver_contract_version",
        ]
        for manifest, output in manifests:
            shard = manifest.output.relative_path
            if existing.get(shard) == manifest.output.sha256:
                reused += 1
                continue
            connection.execute("DELETE FROM asset_observations WHERE shard = ?", (shard,))
            inserted = 0
            for batch in pq.ParquetFile(output).iter_batches(
                batch_size=batch_size, columns=columns
            ):
                values = []
                for row in batch.to_pylist():
                    expires = row["image_url_expires_at"]
                    retry = row["retry_after"]
                    values.append(
                        (
                            shard,
                            row["provider"],
                            row["status"],
                            row["canonical_reference"],
                            row["provider_asset_id"],
                            row["image_url"],
                            row["page_url"],
                            expires.isoformat() if expires is not None else None,
                            row["license_id"],
                            int(row["category_truncated"]),
                            retry.isoformat() if retry is not None else None,
                            row["resolver_contract_version"],
                        )
                    )
                connection.executemany(
                    "INSERT INTO asset_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
                inserted += len(values)
            if inserted != manifest.output.row_count:
                raise ValueError(f"asset catalog row mismatch for {shard}")
            connection.execute(
                "INSERT OR REPLACE INTO asset_shards VALUES (?, ?)",
                (shard, manifest.output.sha256),
            )
            indexed += 1
            indexed_rows += inserted
    emit(
        {
            "event": "metadata_asset_catalog_sync_completed",
            "active_shards": len(manifests),
            "reused_shards": reused,
            "indexed_shards": indexed,
            "indexed_rows": indexed_rows,
        }
    )
