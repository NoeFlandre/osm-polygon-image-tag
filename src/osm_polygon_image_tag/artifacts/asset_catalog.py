import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq

from osm_polygon_image_tag.assets.manifest import AssetManifest
from osm_polygon_image_tag.core.progress import Progress

_ASSET_CATALOG_COLUMNS = (
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
)


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
    connection.execute(
        "CREATE INDEX IF NOT EXISTS asset_observations_duplicate_idx "
        "ON asset_observations (provider, canonical_reference, provider_asset_id, image_url)"
    )


def asset_catalog_columns() -> list[str]:
    """Return the bounded Parquet projection used by the metadata catalog."""
    return list(_ASSET_CATALOG_COLUMNS)


def _observation_values(shard: str, row: Mapping[str, object]) -> tuple[object, ...]:
    expires = row["image_url_expires_at"]
    retry = row["retry_after"]
    return (
        shard,
        row["provider"],
        row["status"],
        row["canonical_reference"],
        row["provider_asset_id"],
        row["image_url"],
        row["page_url"],
        cast(Any, expires).isoformat() if expires is not None else None,
        row["license_id"],
        int(cast(Any, row["category_truncated"])),
        cast(Any, retry).isoformat() if retry is not None else None,
        row["resolver_contract_version"],
    )


def _iter_observation_batches(
    output: Path, shard: str, batch_size: int
) -> Iterator[list[tuple[object, ...]]]:
    for batch in pq.ParquetFile(output).iter_batches(
        batch_size=batch_size, columns=asset_catalog_columns()
    ):
        yield [_observation_values(shard, row) for row in batch.to_pylist()]


def _remove_stale_shards(
    connection: sqlite3.Connection,
    existing: Mapping[str, str],
    active: Mapping[str, str],
) -> None:
    for shard in sorted(set(existing) - set(active)):
        connection.execute("DELETE FROM asset_observations WHERE shard = ?", (shard,))
        connection.execute("DELETE FROM asset_shards WHERE path = ?", (shard,))


def _index_shard(
    connection: sqlite3.Connection,
    manifest: AssetManifest,
    output: Path,
    batch_size: int,
) -> int:
    shard = manifest.output.relative_path
    connection.execute("DELETE FROM asset_observations WHERE shard = ?", (shard,))
    inserted = 0
    for values in _iter_observation_batches(output, shard, batch_size):
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
    return inserted


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
        _remove_stale_shards(connection, existing, active)
        for manifest, output in manifests:
            shard = manifest.output.relative_path
            if existing.get(shard) == manifest.output.sha256:
                reused += 1
                continue
            inserted = _index_shard(connection, manifest, output, batch_size)
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
