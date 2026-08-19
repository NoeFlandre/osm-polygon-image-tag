import sqlite3
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

import pyarrow.parquet as pq

from osm_polygon_image_tag.artifacts.manifest_inventory import verified_manifests
from osm_polygon_image_tag.core.contracts import IMAGE_REFERENCE_KEYS, PANORAMAX_VALUES_COLUMN
from osm_polygon_image_tag.core.manifest import Manifest
from osm_polygon_image_tag.core.progress import Progress

PROVIDERS = IMAGE_REFERENCE_KEYS


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
    connection.execute("CREATE INDEX IF NOT EXISTS observations_shard_idx ON observations (shard)")
    return connection


def sync_catalog(
    data_root: Path,
    *,
    manifests: Sequence[tuple[Manifest, Path]] | None = None,
    catalog_path: Path | None = None,
    batch_size: int = 8192,
    progress: Progress | None = None,
) -> Path:
    emit = progress or (lambda _event: None)
    catalog_path = catalog_path or data_root / "catalog" / "catalog.sqlite"
    selected = list(manifests) if manifests is not None else verified_manifests(data_root)
    emit(
        {
            "event": "metadata_catalog_sync_started",
            "active_shards": len(selected),
            "shard_count": len(selected),
        }
    )
    active = {manifest.output.relative_path: manifest.output.sha256 for manifest, _ in selected}
    with _connect(catalog_path) as connection:
        existing = dict(connection.execute("SELECT path, sha256 FROM shards"))
        stale_shards = sorted(set(existing) - set(active))
        _remove_stale_shards(connection, stale_shards)
        reused, indexed, indexed_rows = _sync_catalog_shards(
            connection,
            selected,
            existing=existing,
            batch_size=batch_size,
            emit=emit,
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


def _sync_catalog_shards(
    connection: sqlite3.Connection,
    selected: Sequence[tuple[Manifest, Path]],
    *,
    existing: dict[str, str],
    batch_size: int,
    emit: Progress,
) -> tuple[int, int, int]:
    reused = indexed = indexed_rows = 0
    for shard_index, (manifest, output) in enumerate(selected, start=1):
        shard = manifest.output.relative_path
        if existing.get(shard) == manifest.output.sha256:
            reused += 1
            continue
        inserted = _sync_catalog_shard(
            connection,
            manifest,
            output,
            shard=shard,
            shard_index=shard_index,
            selected=selected,
            batch_size=batch_size,
            emit=emit,
        )
        indexed += 1
        indexed_rows += inserted
    return reused, indexed, indexed_rows


def _sync_catalog_shard(
    connection: sqlite3.Connection,
    manifest: Manifest,
    output: Path,
    *,
    shard: str,
    shard_index: int,
    selected: Sequence[tuple[Manifest, Path]],
    batch_size: int,
    emit: Progress,
) -> int:
    emit(_shard_event("metadata_catalog_shard_started", shard, shard_index, selected, manifest))
    connection.execute("DELETE FROM observations WHERE shard = ?", (shard,))
    inserted = _index_shard(connection, shard, output, batch_size=batch_size)
    if inserted != manifest.output.row_count:
        raise ValueError(f"catalog row mismatch for {shard}")
    connection.execute(
        "INSERT OR REPLACE INTO shards (path, sha256) VALUES (?, ?)",
        (shard, manifest.output.sha256),
    )
    emit(
        _shard_event(
            "metadata_catalog_shard_completed",
            shard,
            shard_index,
            selected,
            manifest,
            row_count=inserted,
        )
    )
    return inserted


def _remove_stale_shards(connection: sqlite3.Connection, stale_shards: Sequence[str]) -> None:
    for stale in stale_shards:
        connection.execute("DELETE FROM observations WHERE shard = ?", (stale,))
        connection.execute("DELETE FROM shards WHERE path = ?", (stale,))


def _shard_columns() -> list[str]:
    return [
        "osm_type",
        "osm_id",
        "osm_version",
        "geometry_type",
        "area_m2",
        "osm_timestamp",
        *PROVIDERS,
        PANORAMAX_VALUES_COLUMN,
    ]


def _provider_mask(row: dict[str, object]) -> int:
    return sum(
        1 << index
        for index, provider in enumerate(PROVIDERS)
        if (
            bool(row[PANORAMAX_VALUES_COLUMN])
            if provider == "panoramax"
            else row[provider] is not None
        )
    )


def _catalog_row(shard: str, row: dict[str, object]) -> tuple[object, ...]:
    timestamp = row["osm_timestamp"]
    timestamp_value = timestamp.isoformat() if isinstance(timestamp, datetime | date) else None
    return (
        shard,
        row["osm_type"],
        row["osm_id"],
        row["osm_version"],
        row["geometry_type"],
        row["area_m2"],
        timestamp_value,
        _provider_mask(row),
    )


def _index_shard(
    connection: sqlite3.Connection, shard: str, output: Path, *, batch_size: int
) -> int:
    inserted = 0
    for batch in pq.ParquetFile(output).iter_batches(
        batch_size=batch_size, columns=_shard_columns()
    ):
        values = [_catalog_row(shard, row) for row in batch.to_pylist()]
        connection.executemany("INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values)
        inserted += len(values)
    return inserted


def _shard_event(
    event: str,
    shard: str,
    shard_index: int,
    selected: Sequence[tuple[Manifest, Path]],
    manifest: Manifest,
    *,
    row_count: int | None = None,
) -> dict[str, object]:
    return {
        "event": event,
        "shard": shard,
        "shard_index": shard_index,
        "shard_count": len(selected),
        "row_count": manifest.output.row_count if row_count is None else row_count,
    }
