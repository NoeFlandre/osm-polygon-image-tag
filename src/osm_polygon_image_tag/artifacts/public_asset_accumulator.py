"""Bounded SQLite accumulation for public image and relationship rows."""

from __future__ import annotations

import json
import pickle
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path

from osm_polygon_image_tag.artifacts.public_asset_checkpoint import (
    PUBLIC_ASSET_DEDUP_CHECKPOINT_SCHEMA_VERSION,
    PUBLIC_ASSET_SQLITE_CACHE_KIB,
    PUBLIC_ASSET_SQLITE_MMAP_BYTES,
    PUBLIC_ASSET_SQLITE_PAGE_SIZE,
    _remove_incompatible_checkpoint,
    is_compatible_asset_checkpoint,
)
from osm_polygon_image_tag.artifacts.public_asset_rows import (
    _ASSET_DEDUP_COLUMNS,  # noqa: F401 - compatibility import
    _append_batch_row,  # noqa: F401 - compatibility import
    _AssetBatch,
    _AssetColumns,  # noqa: F401 - compatibility import
    _BatchValues,
    _ColumnarAssetRow,  # noqa: F401 - compatibility import
    _deduplicate_batch_values,
    _deduplicate_values,  # noqa: F401 - compatibility import
    _digest,  # noqa: F401 - compatibility import
    _image_identity_values,  # noqa: F401 - compatibility import
    _image_payload,  # noqa: F401 - compatibility import
    _image_value_wins,  # noqa: F401 - compatibility import
    _iter_batches,  # noqa: F401 - compatibility import
    _link_payload,  # noqa: F401 - compatibility import
    _prepare_batch_values,
    _prepare_columnar_batch_values,
    _quality_rank,  # noqa: F401 - compatibility import
    _quality_rank_values,  # noqa: F401 - compatibility import
    image_id,
    image_identity,
)


def _insert_batch_values(connection: sqlite3.Connection, values: _BatchValues) -> None:
    connection.executemany(
        """
        INSERT INTO images(image_key, payload, rank, sort_key)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(image_key) DO UPDATE SET
          payload = excluded.payload,
          rank = excluded.rank,
          sort_key = excluded.sort_key
        WHERE excluded.rank > images.rank
           OR (excluded.rank = images.rank AND excluded.sort_key < images.sort_key)
        """,
        values.image_values,
    )
    connection.executemany(
        "INSERT OR IGNORE INTO image_sources VALUES (?, ?)", values.image_source_values
    )
    connection.executemany("INSERT OR IGNORE INTO links VALUES (?, ?)", values.link_values)
    connection.executemany(
        "INSERT OR IGNORE INTO link_sources VALUES (?, ?)", values.link_source_values
    )
    connection.executemany(
        "INSERT OR IGNORE INTO link_versions VALUES (?, ?)", values.link_version_values
    )


def _open_asset_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(f"PRAGMA page_size={PUBLIC_ASSET_SQLITE_PAGE_SIZE}")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute(f"PRAGMA cache_size=-{PUBLIC_ASSET_SQLITE_CACHE_KIB}")
    connection.execute(f"PRAGMA mmap_size={PUBLIC_ASSET_SQLITE_MMAP_BYTES}")
    connection.execute("PRAGMA locking_mode=EXCLUSIVE")
    return connection


def _initialize_asset_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS images (
            image_key BLOB PRIMARY KEY,
            payload BLOB NOT NULL,
            rank INTEGER NOT NULL,
            sort_key TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS image_sources (
            image_key BLOB NOT NULL,
            source_pbf TEXT NOT NULL,
            PRIMARY KEY (image_key, source_pbf)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS links (
            link_key BLOB PRIMARY KEY,
            payload BLOB NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS link_sources (
            link_key BLOB NOT NULL,
            source_pbf TEXT NOT NULL,
            PRIMARY KEY (link_key, source_pbf)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS link_versions (
            link_key BLOB NOT NULL,
            osm_version INTEGER NOT NULL,
            PRIMARY KEY (link_key, osm_version)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS checkpoint_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS checkpoint_sources (
            source_index INTEGER PRIMARY KEY,
            source_sha256 TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            orphan_count INTEGER NOT NULL
        ) WITHOUT ROWID;
        """
    )


def _initialize_checkpoint_metadata(
    connection: sqlite3.Connection,
    input_hashes: Sequence[str],
    polygon_fingerprint: str,
) -> None:
    values = (
        ("schema_version", str(PUBLIC_ASSET_DEDUP_CHECKPOINT_SCHEMA_VERSION)),
        ("input_hashes", json.dumps(input_hashes, separators=(",", ":"))),
        ("polygon_fingerprint", polygon_fingerprint),
    )
    connection.executemany(
        "INSERT OR IGNORE INTO checkpoint_metadata(key, value) VALUES (?, ?)", values
    )
    connection.commit()


class _Accumulator:
    """Bounded-memory SQLite accumulator for public image relationships."""

    def __init__(
        self,
        path: Path,
        *,
        input_hashes: Sequence[str] | None = None,
        polygon_fingerprint: str | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self.path = path
        self.input_hashes = tuple(input_hashes) if input_hashes is not None else None
        self.polygon_fingerprint = polygon_fingerprint or ""
        self._transaction_input_rows = 0
        self._transaction_orphan_rows = 0
        _remove_incompatible_checkpoint(path, self.input_hashes, self.polygon_fingerprint)
        self.connection = _open_asset_connection(path)
        if max_bytes is not None:
            self._set_max_size(max_bytes)
        _initialize_asset_schema(self.connection)
        self.input_rows, self.orphan_rows = self._initial_counts()

    def _initial_counts(self) -> tuple[int, int]:
        if self.input_hashes is None:
            return 0, 0
        _initialize_checkpoint_metadata(
            self.connection, self.input_hashes, self.polygon_fingerprint
        )
        return self._completed_counts()

    def _set_max_size(self, max_bytes: int) -> None:
        page_size = int(self.connection.execute("PRAGMA page_size").fetchone()[0])
        max_pages = max(1, max_bytes // page_size)
        current_pages = int(self.connection.execute("PRAGMA page_count").fetchone()[0])
        if current_pages > max_pages:
            raise RuntimeError(
                "asset checkpoint already exceeds the safe local-storage limit; "
                "free local space or use the durable checkpoint"
            )
        self.connection.execute(f"PRAGMA max_page_count={max_pages}")

    @staticmethod
    def _is_compatible_checkpoint(
        path: Path,
        input_hashes: Sequence[str],
        polygon_fingerprint: str,
    ) -> bool:
        return is_compatible_asset_checkpoint(path, input_hashes, polygon_fingerprint)

    def _completed_counts(self) -> tuple[int, int]:
        row = self.connection.execute(
            "SELECT COALESCE(SUM(row_count), 0), COALESCE(SUM(orphan_count), 0) "
            "FROM checkpoint_sources"
        ).fetchone()
        return (int(row[0]), int(row[1])) if row is not None else (0, 0)

    def source_completed(self, source_index: int, source_sha256: str) -> bool:
        if self.input_hashes is None:
            return False
        row = self.connection.execute(
            "SELECT source_sha256 FROM checkpoint_sources WHERE source_index = ?",
            (source_index,),
        ).fetchone()
        return row is not None and row[0] == source_sha256

    def begin_source(self) -> None:
        if self.input_hashes is None:
            return
        self.connection.execute("BEGIN")
        self._transaction_input_rows = 0
        self._transaction_orphan_rows = 0

    def complete_source(
        self,
        source_index: int,
        source_sha256: str,
        row_count: int,
        orphan_count: int,
    ) -> None:
        if self.input_hashes is None:
            return
        self.connection.execute(
            "INSERT OR REPLACE INTO checkpoint_sources "
            "(source_index, source_sha256, row_count, orphan_count) VALUES (?, ?, ?, ?)",
            (source_index, source_sha256, row_count, orphan_count),
        )
        self.connection.commit()
        self._transaction_input_rows = 0
        self._transaction_orphan_rows = 0

    def rollback_source(self) -> None:
        if self.input_hashes is None:
            return
        self.connection.rollback()
        self.input_rows -= self._transaction_input_rows
        self.orphan_rows -= self._transaction_orphan_rows
        self._transaction_input_rows = 0
        self._transaction_orphan_rows = 0

    def add(self, row: Mapping[str, object], polygon: Mapping[str, object] | None) -> None:
        self.add_many(((row, polygon),))

    def add_many(
        self,
        rows: Iterable[tuple[Mapping[str, object], Mapping[str, object] | None]],
    ) -> None:
        """Insert one Parquet batch with bulk SQLite operations."""
        prepared = _prepare_batch_values(rows)
        self._add_prepared(prepared)

    def add_batch(
        self,
        batch: _AssetBatch,
        canonical_polygons: Mapping[tuple[str, int], Mapping[str, object]],
    ) -> None:
        """Insert a column-oriented Parquet batch without row-dict materialization."""
        self._add_prepared(_prepare_columnar_batch_values(batch, canonical_polygons))

    def _add_prepared(self, prepared: _BatchValues) -> None:
        self.input_rows += prepared.input_rows
        self._transaction_input_rows += prepared.input_rows
        self.orphan_rows += prepared.orphan_rows
        self._transaction_orphan_rows += prepared.orphan_rows
        if not prepared.image_values:
            return
        _deduplicate_batch_values(prepared)
        _insert_batch_values(self.connection, prepared)

    def _grouped_values(
        self,
        table: str,
        key_column: str,
        value_column: str,
        converter: Callable[[object], object],
    ) -> Iterator[tuple[bytes, list[object]]]:
        """Read an indexed side table in key order, without one query per row."""
        separator = "\x1f"
        queries = {
            ("image_sources", "image_key", "source_pbf"): """
                SELECT image_key, GROUP_CONCAT(source_pbf, ?)
                FROM (
                    SELECT image_key, source_pbf
                    FROM image_sources
                    ORDER BY image_key, source_pbf
                )
                GROUP BY image_key
                ORDER BY image_key
            """,
            ("link_sources", "link_key", "source_pbf"): """
                SELECT link_key, GROUP_CONCAT(source_pbf, ?)
                FROM (
                    SELECT link_key, source_pbf
                    FROM link_sources
                    ORDER BY link_key, source_pbf
                )
                GROUP BY link_key
                ORDER BY link_key
            """,
            ("link_versions", "link_key", "osm_version"): """
                SELECT link_key, GROUP_CONCAT(osm_version, ?)
                FROM (
                    SELECT link_key, osm_version
                    FROM link_versions
                    ORDER BY link_key, osm_version
                )
                GROUP BY link_key
                ORDER BY link_key
            """,
        }
        query = queries[(table, key_column, value_column)]
        for key, values in self.connection.execute(query, (separator,)):
            if values is None:
                yield bytes(key), []
            else:
                yield bytes(key), [converter(value) for value in str(values).split(separator)]

    @staticmethod
    def _take_group(
        groups: Iterator[tuple[bytes, list[object]]],
        current: tuple[bytes, list[object]] | None,
        key: bytes,
    ) -> tuple[list[object], tuple[bytes, list[object]] | None]:
        while current is not None and current[0] < key:
            current = next(groups, None)
        if current is not None and current[0] == key:
            return current[1], next(groups, None)
        return [], current

    def images(self) -> Iterator[dict[str, object]]:
        query = "SELECT image_key, payload FROM images ORDER BY image_key"
        groups = self._grouped_values("image_sources", "image_key", "source_pbf", str)
        group = next(groups, None)
        for image_key, payload in self.connection.execute(query):
            row = pickle.loads(payload)  # noqa: S301 - database is created above
            if not isinstance(row, dict):
                raise TypeError("invalid public image payload")
            values, group = self._take_group(groups, group, bytes(image_key))
            row["source_pbfs"] = [str(value) for value in values]
            yield row

    def links(self) -> Iterator[dict[str, object]]:
        query = "SELECT link_key, payload FROM links ORDER BY link_key"
        source_groups = self._grouped_values("link_sources", "link_key", "source_pbf", str)
        version_groups = self._grouped_values(
            "link_versions", "link_key", "osm_version", lambda value: int(str(value))
        )
        source_group = next(source_groups, None)
        version_group = next(version_groups, None)
        for link_key, payload in self.connection.execute(query):
            row = pickle.loads(payload)  # noqa: S301 - database is created above
            if not isinstance(row, dict):
                raise TypeError("invalid public link payload")
            key = bytes(link_key)
            sources, source_group = self._take_group(source_groups, source_group, key)
            versions, version_group = self._take_group(version_groups, version_group, key)
            row["source_pbfs"] = [str(value) for value in sources]
            row["observed_osm_versions"] = [int(str(value)) for value in versions]
            yield row

    def counts(self) -> tuple[int, int]:
        image_rows = int(self.connection.execute("SELECT COUNT(*) FROM images").fetchone()[0])
        link_rows = int(self.connection.execute("SELECT COUNT(*) FROM links").fetchone()[0])
        return image_rows, link_rows

    def close(self) -> None:
        if self.connection.in_transaction:
            if self.input_hashes is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        self.connection.close()


__all__ = ["image_id", "image_identity"]
