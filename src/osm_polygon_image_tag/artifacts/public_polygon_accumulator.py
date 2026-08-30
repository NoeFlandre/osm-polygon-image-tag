"""SQLite-backed polygon accumulation for public dataset materialization."""

from __future__ import annotations

import json
import pickle
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from osm_polygon_image_tag.artifacts.checkpoints import remove_checkpoint_files
from osm_polygon_image_tag.core.serialization import canonical_json

PUBLIC_DEDUP_CHECKPOINT_SCHEMA_VERSION = 1


def _identity(row: Mapping[str, Any]) -> tuple[str, int]:
    return (str(row["osm_type"]), int(row["osm_id"]))


def _polygon_rank(row: Mapping[str, Any]) -> tuple[int, int, int, str]:
    version = row.get("osm_version")
    timestamp = row.get("osm_timestamp")
    timestamp_value = timestamp.isoformat() if isinstance(timestamp, datetime | date) else ""
    return (
        1 if version is not None else 0,
        int(version) if version is not None else -1,
        1 if timestamp is not None else 0,
        timestamp_value,
    )


def _stable_row_key(row: dict[str, Any]) -> str:
    return canonical_json(row)


class _PolygonAccumulator:
    """Keep polygon selection and provenance on disk instead of in RAM."""

    def __init__(self, path: Path, *, input_hashes: Sequence[str] | None = None) -> None:
        self.path = path
        self.input_hashes = tuple(input_hashes) if input_hashes is not None else None
        self._transaction_input_rows = 0
        _remove_incompatible_polygon_checkpoint(path, self.input_hashes)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute("PRAGMA cache_size=-32768")
        self.connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS polygons (
                osm_type TEXT NOT NULL,
                osm_id INTEGER NOT NULL,
                source_pbf TEXT NOT NULL,
                source_feature_id TEXT NOT NULL,
                rank_version_present INTEGER NOT NULL,
                rank_version INTEGER NOT NULL,
                rank_timestamp_present INTEGER NOT NULL,
                rank_timestamp TEXT NOT NULL,
                sort_key TEXT NOT NULL,
                payload BLOB NOT NULL,
                PRIMARY KEY (osm_type, osm_id)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS polygon_sources (
                osm_type TEXT NOT NULL,
                osm_id INTEGER NOT NULL,
                source_pbf TEXT NOT NULL,
                PRIMARY KEY (osm_type, osm_id, source_pbf)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS checkpoint_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS checkpoint_sources (
                source_index INTEGER PRIMARY KEY,
                source_sha256 TEXT NOT NULL,
                row_count INTEGER NOT NULL
            ) WITHOUT ROWID;
            """
        )
        self.input_rows = _initialize_polygon_checkpoint(self.connection, self.input_hashes, self)

    @staticmethod
    def _is_compatible_checkpoint(path: Path, input_hashes: Sequence[str]) -> bool:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(path)
            metadata = _polygon_checkpoint_metadata(connection)
            return _polygon_checkpoint_metadata_matches(metadata, input_hashes) and (
                _polygon_checkpoint_sources_match(connection, input_hashes)
            )
        except (
            OSError,
            sqlite3.DatabaseError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return False
        finally:
            if connection is not None:
                connection.close()

    def _completed_input_rows(self) -> int:
        result = self.connection.execute(
            "SELECT COALESCE(SUM(row_count), 0) FROM checkpoint_sources"
        ).fetchone()
        return int(result[0]) if result is not None else 0

    def source_completed(self, source_index: int, source_sha256: str) -> bool:
        if self.input_hashes is None:
            return False
        row = self.connection.execute(
            "SELECT source_sha256 FROM checkpoint_sources WHERE source_index = ?",
            (source_index,),
        ).fetchone()
        return row is not None and row[0] == source_sha256

    def all_sources_completed(self) -> bool:
        """Return whether every input shard has a committed checkpoint marker."""
        if self.input_hashes is None:
            return False
        completed = self.connection.execute(
            "SELECT source_index, source_sha256 FROM checkpoint_sources ORDER BY source_index"
        ).fetchall()
        return len(completed) == len(self.input_hashes) and _completed_sources_match(
            completed, self.input_hashes
        )

    def unique_count(self) -> int:
        """Return the number of canonical polygon rows recorded in SQLite."""
        row = self.connection.execute("SELECT COUNT(*) FROM polygons").fetchone()
        return int(row[0]) if row is not None else 0

    def public_output_sha256(self) -> str | None:
        """Return the digest recorded for the finalized public polygon file."""
        row = self.connection.execute(
            "SELECT value FROM checkpoint_metadata WHERE key = 'public_output_sha256'"
        ).fetchone()
        return str(row[0]) if row is not None else None

    def public_output_rows(self) -> int | None:
        """Return the recorded public polygon row count, when available."""
        row = self.connection.execute(
            "SELECT value FROM checkpoint_metadata WHERE key = 'public_output_rows'"
        ).fetchone()
        if row is None:
            return None
        try:
            value = int(row[0])
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    def record_public_output(self, sha256: str, row_count: int) -> None:
        """Record a validated public polygon digest for safe output reuse."""
        if row_count < 0:
            raise ValueError("public polygon row count must be non-negative")
        self.connection.execute(
            "INSERT OR REPLACE INTO checkpoint_metadata(key, value) VALUES (?, ?)",
            ("public_output_sha256", sha256),
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO checkpoint_metadata(key, value) VALUES (?, ?)",
            ("public_output_rows", str(row_count)),
        )
        self.connection.commit()

    def begin_source(self) -> None:
        if self.input_hashes is None:
            return
        self.connection.execute("BEGIN")
        self._transaction_input_rows = 0

    def complete_source(self, source_index: int, source_sha256: str, row_count: int) -> None:
        if self.input_hashes is None:
            return
        self.connection.execute(
            "INSERT OR REPLACE INTO checkpoint_sources(source_index, source_sha256, row_count) "
            "VALUES (?, ?, ?)",
            (source_index, source_sha256, row_count),
        )
        self.connection.commit()
        self._transaction_input_rows = 0

    def rollback_source(self) -> None:
        if self.input_hashes is None:
            return
        self.connection.rollback()
        self.input_rows -= self._transaction_input_rows
        self._transaction_input_rows = 0

    def add(self, row: Mapping[str, Any]) -> None:
        self.add_many([row])

    def add_many(self, rows: Iterable[Mapping[str, Any]]) -> None:
        """Ingest a batch with bulk SQLite calls and SQL-side winner selection."""
        materialized = [dict(row) for row in rows]
        self.input_rows += len(materialized)
        self._transaction_input_rows += len(materialized)
        if not materialized:
            return
        source_values: list[tuple[str, int, str]] = []
        polygon_values: list[tuple[object, ...]] = []
        for row in materialized:
            osm_type, osm_id = _identity(row)
            source = str(row["source_pbf"])
            source_feature = str(row.get("source_feature_id") or "")
            source_values.append((osm_type, osm_id, source))
            rank_version_present, rank_version, rank_timestamp_present, rank_timestamp = (
                _polygon_rank(row)
            )
            polygon_values.append(
                (
                    osm_type,
                    osm_id,
                    source,
                    source_feature,
                    rank_version_present,
                    rank_version,
                    rank_timestamp_present,
                    rank_timestamp,
                    _stable_row_key(row),
                    sqlite3.Binary(pickle.dumps(row, protocol=5)),
                )
            )
        self.connection.executemany(
            "INSERT OR IGNORE INTO polygon_sources VALUES (?, ?, ?)",
            source_values,
        )
        self.connection.executemany(
            """
            INSERT INTO polygons(
                osm_type, osm_id, source_pbf, source_feature_id,
                rank_version_present, rank_version, rank_timestamp_present,
                rank_timestamp, sort_key, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(osm_type, osm_id) DO UPDATE SET
                source_pbf = excluded.source_pbf,
                source_feature_id = excluded.source_feature_id,
                rank_version_present = excluded.rank_version_present,
                rank_version = excluded.rank_version,
                rank_timestamp_present = excluded.rank_timestamp_present,
                rank_timestamp = excluded.rank_timestamp,
                sort_key = excluded.sort_key,
                payload = excluded.payload
            WHERE excluded.rank_version_present > polygons.rank_version_present
               OR (
                    excluded.rank_version_present = polygons.rank_version_present
                AND excluded.rank_version > polygons.rank_version
               )
               OR (
                    excluded.rank_version_present = polygons.rank_version_present
                AND excluded.rank_version = polygons.rank_version
                AND excluded.rank_timestamp_present > polygons.rank_timestamp_present
               )
               OR (
                    excluded.rank_version_present = polygons.rank_version_present
                AND excluded.rank_version = polygons.rank_version
                AND excluded.rank_timestamp_present = polygons.rank_timestamp_present
                AND excluded.rank_timestamp > polygons.rank_timestamp
               )
               OR (
                    excluded.rank_version_present = polygons.rank_version_present
                AND excluded.rank_version = polygons.rank_version
                AND excluded.rank_timestamp_present = polygons.rank_timestamp_present
                AND excluded.rank_timestamp = polygons.rank_timestamp
                AND (
                    excluded.source_pbf,
                    excluded.source_feature_id,
                    excluded.sort_key
                ) < (
                    polygons.source_pbf,
                    polygons.source_feature_id,
                    polygons.sort_key
                )
               )
            """,
            polygon_values,
        )

    def rows(self) -> Iterator[dict[str, Any]]:
        source_groups = _polygon_source_groups(self.connection)
        group = next(source_groups, None)
        for osm_type, osm_id, payload in self.connection.execute(
            "SELECT osm_type, osm_id, payload FROM polygons ORDER BY osm_type, osm_id"
        ):
            group = _advance_polygon_source_group(group, source_groups, (osm_type, osm_id))
            row = _polygon_row_with_sources(payload, group)
            group = next(source_groups, None)
            yield row

    def close(self) -> None:
        if self.connection.in_transaction:
            if self.input_hashes is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        self.connection.close()


def _remove_incompatible_polygon_checkpoint(path: Path, input_hashes: Sequence[str] | None) -> None:
    if (
        input_hashes is not None
        and path.is_file()
        and not _PolygonAccumulator._is_compatible_checkpoint(path, input_hashes)
    ):
        remove_checkpoint_files(path)


def _initialize_polygon_checkpoint(
    connection: sqlite3.Connection,
    input_hashes: Sequence[str] | None,
    accumulator: _PolygonAccumulator,
) -> int:
    if input_hashes is None:
        return 0
    values = (
        ("schema_version", str(PUBLIC_DEDUP_CHECKPOINT_SCHEMA_VERSION)),
        ("input_hashes", json.dumps(input_hashes, separators=(",", ":"))),
    )
    connection.executemany(
        "INSERT OR IGNORE INTO checkpoint_metadata(key, value) VALUES (?, ?)",
        values,
    )
    connection.commit()
    return accumulator._completed_input_rows()


def _polygon_source_groups(
    connection: sqlite3.Connection,
) -> Iterator[tuple[str, int, str | None]]:
    return iter(
        connection.execute(
            """
            SELECT osm_type, osm_id, GROUP_CONCAT(source_pbf, ?)
            FROM (
                SELECT osm_type, osm_id, source_pbf
                FROM polygon_sources
                ORDER BY osm_type, osm_id, source_pbf
            )
            GROUP BY osm_type, osm_id
            ORDER BY osm_type, osm_id
            """,
            ("\x1f",),
        )
    )


def _advance_polygon_source_group(
    group: tuple[str, int, str | None] | None,
    groups: Iterator[tuple[str, int, str | None]],
    identity: tuple[str, int],
) -> tuple[str, int, str | None]:
    while group is not None and (group[0], group[1]) < identity:
        group = next(groups, None)
    if group is None or (group[0], group[1]) != identity:
        raise ValueError("polygon accumulator provenance is incomplete")
    return group


def _polygon_row_with_sources(payload: bytes, group: tuple[str, int, str | None]) -> dict[str, Any]:
    row = pickle.loads(payload)  # noqa: S301 - database is created above
    if not isinstance(row, dict):
        raise TypeError("invalid polygon accumulator payload")
    row["source_pbfs"] = _source_pbf_values(group[2])
    return row


def _source_pbf_values(value: str | None) -> list[str]:
    return str(value or "").split("\x1f") if value else []


def _polygon_checkpoint_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return dict(connection.execute("SELECT key, value FROM checkpoint_metadata").fetchall())


def _polygon_checkpoint_metadata_matches(
    metadata: Mapping[str, str], input_hashes: Sequence[str]
) -> bool:
    return metadata.get("schema_version") == str(
        PUBLIC_DEDUP_CHECKPOINT_SCHEMA_VERSION
    ) and json.loads(metadata["input_hashes"]) == list(input_hashes)


def _polygon_checkpoint_sources_match(
    connection: sqlite3.Connection, input_hashes: Sequence[str]
) -> bool:
    for source_index, source_sha256, row_count in connection.execute(
        "SELECT source_index, source_sha256, row_count FROM checkpoint_sources"
    ):
        if not _valid_polygon_checkpoint_source(
            source_index, source_sha256, row_count, input_hashes
        ):
            return False
    return True


def _completed_sources_match(
    completed: Sequence[tuple[int, str]], input_hashes: Sequence[str]
) -> bool:
    return all(
        source_index == expected_index and source_sha256 == input_hashes[expected_index]
        for expected_index, (source_index, source_sha256) in enumerate(completed)
    )


def _valid_polygon_checkpoint_source(
    source_index: int,
    source_sha256: str,
    row_count: int,
    input_hashes: Sequence[str],
) -> bool:
    return (
        0 <= source_index < len(input_hashes)
        and input_hashes[source_index] == source_sha256
        and row_count >= 0
    )
