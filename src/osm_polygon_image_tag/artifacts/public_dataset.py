"""Build the deduplicated, publishable view from resumable internal shards."""

from __future__ import annotations

import json
import os
import pickle
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from osm_polygon_image_tag.artifacts.asset_inventory import verified_asset_manifests
from osm_polygon_image_tag.artifacts.checkpoints import remove_checkpoint_files
from osm_polygon_image_tag.artifacts.manifest_inventory import verified_manifests
from osm_polygon_image_tag.artifacts.public_assets import (
    PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE,
    PublicAssetsResult,
    build_public_asset_tables,
    validate_public_image_parquet,
    validate_public_link_parquet,
)
from osm_polygon_image_tag.core.atomic import atomic_write_bytes
from osm_polygon_image_tag.core.manifest import (
    DATASET_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    PROCESSING_CONTRACT_VERSION,
    Manifest,
    OutputIdentity,
    RunCounts,
    SourceIdentity,
    file_sha256,
)
from osm_polygon_image_tag.core.schema import dataset_schema

PUBLIC_SCHEMA_VERSION = 2
PUBLIC_POLYGON_RELATIVE = "public/polygons.parquet"
LEGACY_PUBLIC_ASSET_RELATIVE = "public/image_assets.parquet"
PUBLIC_IMAGE_RELATIVE = "public/images.parquet"
PUBLIC_LINK_RELATIVE = "public/polygon_images.parquet"
PUBLIC_DEDUP_CHECKPOINT_RELATIVE = "tmp/.public-polygons.sqlite"
# Kept as an import-compatible alias for callers that only need the image file.
PUBLIC_ASSET_RELATIVE = PUBLIC_IMAGE_RELATIVE
PUBLIC_MANIFEST_RELATIVE = "public/public-manifest.json"
PUBLIC_DEDUP_CHECKPOINT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class PublicDatasetResult:
    """Publishable deduplicated artifacts and their data-derived counts."""

    polygon_path: Path
    image_path: Path
    link_path: Path
    manifest_path: Path
    polygon_manifest: Manifest
    polygon_rows: int
    image_rows: int
    link_rows: int
    duplicate_polygon_rows: int
    duplicate_image_rows: int
    duplicate_link_rows: int
    orphan_asset_rows: int
    reused: bool = False


def public_polygon_schema() -> pa.Schema:
    """Return the public polygon schema with complete source provenance."""
    fields = list(dataset_schema())
    fields.append(pa.field("source_pbfs", pa.list_(pa.string()), nullable=False))
    metadata = dict(dataset_schema().metadata or {})
    metadata[b"osm_polygon_image_tag_public_schema_version"] = str(PUBLIC_SCHEMA_VERSION).encode()
    return pa.schema(fields, metadata=metadata)


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


def _jsonable(value: object) -> object:
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _stable_row_key(row: dict[str, Any]) -> str:
    return json.dumps(_jsonable(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _PolygonAccumulator:
    """Keep polygon selection and provenance on disk instead of in RAM."""

    def __init__(self, path: Path, *, input_hashes: Sequence[str] | None = None) -> None:
        self.path = path
        self.input_hashes = tuple(input_hashes) if input_hashes is not None else None
        self._transaction_input_rows = 0
        if (
            self.input_hashes is not None
            and path.is_file()
            and not self._is_compatible_checkpoint(path, self.input_hashes)
        ):
            remove_checkpoint_files(path)
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
        if self.input_hashes is None:
            self.input_rows = 0
        else:
            self.connection.execute(
                "INSERT OR IGNORE INTO checkpoint_metadata(key, value) VALUES (?, ?)",
                (
                    "schema_version",
                    str(PUBLIC_DEDUP_CHECKPOINT_SCHEMA_VERSION),
                ),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO checkpoint_metadata(key, value) VALUES (?, ?)",
                ("input_hashes", json.dumps(self.input_hashes, separators=(",", ":"))),
            )
            self.connection.commit()
            self.input_rows = self._completed_input_rows()

    @staticmethod
    def _is_compatible_checkpoint(path: Path, input_hashes: Sequence[str]) -> bool:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(path)
            rows = dict(connection.execute("SELECT key, value FROM checkpoint_metadata").fetchall())
            if rows.get("schema_version") != str(PUBLIC_DEDUP_CHECKPOINT_SCHEMA_VERSION):
                return False
            if json.loads(rows["input_hashes"]) != list(input_hashes):
                return False
            for source_index, source_sha256, row_count in connection.execute(
                "SELECT source_index, source_sha256, row_count FROM checkpoint_sources"
            ):
                if (
                    source_index < 0
                    or source_index >= len(input_hashes)
                    or input_hashes[source_index] != source_sha256
                    or row_count < 0
                ):
                    return False
            return True
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
        return len(completed) == len(self.input_hashes) and all(
            source_index == expected_index and source_sha256 == self.input_hashes[expected_index]
            for expected_index, (source_index, source_sha256) in enumerate(completed)
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
        separator = "\x1f"
        source_groups = self.connection.execute(
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
            (separator,),
        )
        group = next(source_groups, None)
        for osm_type, osm_id, payload in self.connection.execute(
            "SELECT osm_type, osm_id, payload FROM polygons ORDER BY osm_type, osm_id"
        ):
            while group is not None and (group[0], group[1]) < (osm_type, osm_id):
                group = next(source_groups, None)
            if group is None or (group[0], group[1]) != (osm_type, osm_id):
                raise ValueError("polygon accumulator provenance is incomplete")
            row = pickle.loads(payload)  # noqa: S301 - database is created above
            if not isinstance(row, dict):
                raise TypeError("invalid polygon accumulator payload")
            row["source_pbfs"] = str(group[2]).split(separator)
            group = next(source_groups, None)
            yield row

    def close(self) -> None:
        if self.connection.in_transaction:
            if self.input_hashes is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        self.connection.close()


def _iter_source_batches(output: Path, *, batch_size: int = 8192) -> Iterator[list[dict[str, Any]]]:
    for batch in pq.ParquetFile(output).iter_batches(batch_size=batch_size):
        yield batch.to_pylist()


def _write_polygon_rows(
    rows: Iterable[dict[str, Any]], path: Path, *, batch_size: int = 4096
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = public_polygon_schema()
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    count = 0
    try:
        with pq.ParquetWriter(
            temporary_path, schema, compression="zstd", use_dictionary=True, write_statistics=True
        ) as writer:
            batch: list[dict[str, Any]] = []
            for row in rows:
                batch.append(row)
                if len(batch) == batch_size:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                    count += len(batch)
                    batch.clear()
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                count += len(batch)
        _validate_public_polygon(temporary_path, expected_rows=count)
        with temporary_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return count


def _canonical_polygon_index(path: Path) -> dict[tuple[str, int], dict[str, object]]:
    """Read only the small identity columns needed to join asset rows."""
    index: dict[tuple[str, int], dict[str, object]] = {}
    for batch in pq.ParquetFile(path).iter_batches(
        columns=["osm_type", "osm_id", "osm_version"],
        batch_size=65536,
    ):
        for row in batch.to_pylist():
            osm_type = str(row["osm_type"])
            osm_id = int(row["osm_id"])
            index[(osm_type, osm_id)] = {
                "osm_type": osm_type,
                "osm_id": osm_id,
                "osm_version": row.get("osm_version"),
            }
    return index


def _validate_public_polygon(path: Path, *, expected_rows: int | None = None) -> None:
    parquet = pq.ParquetFile(path)
    actual = parquet.schema_arrow
    expected = public_polygon_schema()
    if (
        actual.names != expected.names
        or actual.metadata != expected.metadata
        or any(
            actual_field.type != expected_field.type
            or actual_field.nullable != expected_field.nullable
            for actual_field, expected_field in zip(actual, expected, strict=True)
        )
    ):
        raise ValueError("public polygon Parquet schema does not match")
    if expected_rows is not None and parquet.metadata.num_rows != expected_rows:
        raise ValueError("public polygon row count does not match")


def validate_public_dataset(data_root: Path) -> dict[str, str]:
    """Validate the materialized public files and return their digests.

    The internal per-PBF shards are deliberately not part of this contract:
    they remain available for resume and audit, while only the canonical
    polygons, unique images, and relationship files are eligible for release.
    """
    root = data_root.resolve()
    manifest_path = root / PUBLIC_MANIFEST_RELATIVE
    polygon_path = root / PUBLIC_POLYGON_RELATIVE
    image_path = root / PUBLIC_IMAGE_RELATIVE
    link_path = root / PUBLIC_LINK_RELATIVE
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("public_schema_version") != PUBLIC_SCHEMA_VERSION:
            raise ValueError("unsupported public dataset schema version")
        polygon_output = payload["polygon_output"]
        image_output = payload["image_output"]
        link_output = payload["link_output"]
        if polygon_output["size_bytes"] != polygon_path.stat().st_size:
            raise ValueError("public polygon size mismatch")
        if image_output["size_bytes"] != image_path.stat().st_size:
            raise ValueError("public image size mismatch")
        if link_output["size_bytes"] != link_path.stat().st_size:
            raise ValueError("public link size mismatch")
        if file_sha256(polygon_path) != polygon_output["sha256"]:
            raise ValueError("public polygon digest mismatch")
        if file_sha256(image_path) != image_output["sha256"]:
            raise ValueError("public image digest mismatch")
        if file_sha256(link_path) != link_output["sha256"]:
            raise ValueError("public link digest mismatch")
        _validate_public_polygon(polygon_path, expected_rows=int(polygon_output["row_count"]))
        validate_public_image_parquet(image_path, expected_rows=int(image_output["row_count"]))
        validate_public_link_parquet(link_path, expected_rows=int(link_output["row_count"]))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(str(error) or "public dataset artifacts are missing or invalid") from error
    return {
        PUBLIC_POLYGON_RELATIVE: str(polygon_output["sha256"]),
        PUBLIC_IMAGE_RELATIVE: str(image_output["sha256"]),
        PUBLIC_LINK_RELATIVE: str(link_output["sha256"]),
        PUBLIC_MANIFEST_RELATIVE: file_sha256(manifest_path),
    }


def _public_polygon_manifest(path: Path, rows: int) -> Manifest:
    return Manifest(
        MANIFEST_SCHEMA_VERSION,
        PROCESSING_CONTRACT_VERSION,
        DATASET_SCHEMA_VERSION,
        SourceIdentity("internal/polygon-shards", 0, 0, "0" * 64),
        OutputIdentity(PUBLIC_POLYGON_RELATIVE, path.stat().st_size, file_sha256(path), rows),
        "public-dedup",
        RunCounts(rows, {}),
    )


def _manifest_polygon_row_count(root: Path, output: Path, digest: str) -> int | None:
    """Reuse a matching public-manifest row count without scanning SQLite."""
    try:
        payload = json.loads((root / PUBLIC_MANIFEST_RELATIVE).read_text(encoding="utf-8"))
        polygon_output = payload["polygon_output"]
        if (
            polygon_output["sha256"] != digest
            or int(polygon_output["size_bytes"]) != output.stat().st_size
        ):
            return None
        row_count = int(polygon_output["row_count"])
        return row_count if row_count >= 0 else None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _remove_legacy_public_asset(root: Path) -> None:
    """Remove the exact V1 generated image artifact after V2 is ready."""
    legacy = root / LEGACY_PUBLIC_ASSET_RELATIVE
    if legacy.is_file() and not legacy.is_symlink():
        legacy.unlink()


def _manifest_payload(
    polygon_manifests: Sequence[tuple[Any, Path]],
    asset_manifests: Sequence[tuple[Any, Path]],
    polygon_manifest: Manifest,
    assets: PublicAssetsResult,
    *,
    polygon_rows: int,
    image_rows: int,
    link_rows: int,
    duplicate_polygon_rows: int,
    duplicate_image_rows: int,
    duplicate_link_rows: int,
    orphan_asset_rows: int,
) -> dict[str, Any]:
    def output(path: Path, rows: int) -> dict[str, object]:
        return {
            "sha256": file_sha256(path),
            "size_bytes": path.stat().st_size,
            "row_count": rows,
        }

    return {
        "public_schema_version": PUBLIC_SCHEMA_VERSION,
        "polygon_inputs": [m.output.sha256 for m, _ in polygon_manifests],
        "asset_inputs": [m.output.sha256 for m, _ in asset_manifests],
        "polygon_output": {
            "sha256": polygon_manifest.output.sha256,
            "size_bytes": polygon_manifest.output.size_bytes,
            "row_count": polygon_rows,
        },
        "image_output": output(assets.image_path, image_rows),
        "link_output": output(assets.link_path, link_rows),
        "polygon_rows": polygon_rows,
        "image_rows": image_rows,
        "link_rows": link_rows,
        "duplicate_polygon_rows": duplicate_polygon_rows,
        "duplicate_image_rows": duplicate_image_rows,
        "duplicate_link_rows": duplicate_link_rows,
        "orphan_asset_rows": orphan_asset_rows,
    }


def _try_reuse(
    root: Path,
    polygon_manifests: Sequence[tuple[Any, Path]],
    asset_manifests: Sequence[tuple[Any, Path]],
) -> PublicDatasetResult | None:
    manifest_path = root / PUBLIC_MANIFEST_RELATIVE
    polygon_path = root / PUBLIC_POLYGON_RELATIVE
    image_path = root / PUBLIC_IMAGE_RELATIVE
    link_path = root / PUBLIC_LINK_RELATIVE
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("public_schema_version") != PUBLIC_SCHEMA_VERSION:
            return None
        if payload.get("polygon_inputs") != [m.output.sha256 for m, _ in polygon_manifests]:
            return None
        if payload.get("asset_inputs") != [m.output.sha256 for m, _ in asset_manifests]:
            return None
        if not polygon_path.is_file() or not image_path.is_file() or not link_path.is_file():
            return None
        polygon_manifest = _public_polygon_manifest(polygon_path, int(payload["polygon_rows"]))
        if polygon_manifest.output.sha256 != payload["polygon_output"]["sha256"]:
            return None
        if file_sha256(image_path) != payload["image_output"]["sha256"]:
            return None
        if file_sha256(link_path) != payload["link_output"]["sha256"]:
            return None
        validate_public_dataset(root)
        return PublicDatasetResult(
            polygon_path,
            image_path,
            link_path,
            manifest_path,
            polygon_manifest,
            polygon_manifest.output.row_count,
            int(payload["image_rows"]),
            int(payload["link_rows"]),
            int(payload["duplicate_polygon_rows"]),
            int(payload["duplicate_image_rows"]),
            int(payload["duplicate_link_rows"]),
            int(payload.get("orphan_asset_rows", 0)),
            reused=True,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def build_public_dataset(
    data_root: Path,
    *,
    manifests: Sequence[tuple[Any, Path]] | None = None,
    asset_manifests: Sequence[tuple[Any, Path]] | None = None,
) -> PublicDatasetResult:
    """Materialize a deterministic deduplicated view without touching inputs."""
    root = data_root.resolve()
    polygon_manifests = list(manifests) if manifests is not None else verified_manifests(root)
    source_assets = (
        list(asset_manifests) if asset_manifests is not None else verified_asset_manifests(root)
    )
    reused = _try_reuse(root, polygon_manifests, source_assets)
    if reused is not None:
        remove_checkpoint_files(root / PUBLIC_DEDUP_CHECKPOINT_RELATIVE)
        remove_checkpoint_files(root / PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE)
        _remove_legacy_public_asset(root)
        return reused

    temporary_root = root / "tmp"
    created_temporary_root = not temporary_root.exists()
    temporary_root.mkdir(parents=True, exist_ok=True)
    database_path = root / PUBLIC_DEDUP_CHECKPOINT_RELATIVE
    input_hashes = [manifest.output.sha256 for manifest, _ in polygon_manifests]
    accumulator = _PolygonAccumulator(database_path, input_hashes=input_hashes)
    try:
        for source_index, (manifest, output) in enumerate(polygon_manifests):
            source_sha256 = manifest.output.sha256
            if accumulator.source_completed(source_index, source_sha256):
                continue
            accumulator.begin_source()
            source_rows = 0
            try:
                for batch in _iter_source_batches(output):
                    source_rows += len(batch)
                    accumulator.add_many(batch)
                accumulator.complete_source(source_index, source_sha256, source_rows)
            except BaseException:
                accumulator.rollback_source()
                raise
        input_polygon_rows = accumulator.input_rows
        polygon_path = root / PUBLIC_POLYGON_RELATIVE
        polygon_rows_count = 0
        reuse_polygon = False
        recorded_digest = accumulator.public_output_sha256()
        if (
            accumulator.all_sources_completed()
            and recorded_digest is not None
            and polygon_path.is_file()
        ):
            try:
                polygon_rows_count = accumulator.public_output_rows()
                if polygon_rows_count is None:
                    polygon_rows_count = _manifest_polygon_row_count(
                        root, polygon_path, recorded_digest
                    )
                if polygon_rows_count is None:
                    polygon_rows_count = accumulator.unique_count()
                _validate_public_polygon(polygon_path, expected_rows=polygon_rows_count)
                reuse_polygon = file_sha256(polygon_path) == recorded_digest
            except (OSError, ValueError, pa.ArrowException):
                reuse_polygon = False
        if not reuse_polygon:
            polygon_rows_count = _write_polygon_rows(accumulator.rows(), polygon_path)
            accumulator.record_public_output(file_sha256(polygon_path), polygon_rows_count)
        if polygon_rows_count is None:
            raise RuntimeError("public polygon row count is unavailable")
        canonical_polygons = _canonical_polygon_index(polygon_path)
    finally:
        accumulator.close()
    polygon_manifest = _public_polygon_manifest(polygon_path, polygon_rows_count)
    assets = build_public_asset_tables(
        root,
        source_assets,
        canonical_polygons,
        polygon_fingerprint=polygon_manifest.output.sha256,
    )
    payload = _manifest_payload(
        polygon_manifests,
        source_assets,
        polygon_manifest,
        assets,
        polygon_rows=polygon_rows_count,
        image_rows=assets.image_rows,
        link_rows=assets.link_rows,
        duplicate_polygon_rows=input_polygon_rows - polygon_rows_count,
        duplicate_image_rows=assets.duplicate_image_rows,
        duplicate_link_rows=assets.duplicate_link_rows,
        orphan_asset_rows=assets.orphan_rows,
    )
    atomic_write_bytes(
        root / PUBLIC_MANIFEST_RELATIVE,
        (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
        prefix=".public-manifest.",
        suffix=".tmp",
        sync_directory=True,
    )
    _remove_legacy_public_asset(root)
    remove_checkpoint_files(database_path)
    if created_temporary_root and not any(temporary_root.iterdir()):
        temporary_root.rmdir()
    return PublicDatasetResult(
        polygon_path,
        assets.image_path,
        assets.link_path,
        root / PUBLIC_MANIFEST_RELATIVE,
        polygon_manifest,
        polygon_rows_count,
        assets.image_rows,
        assets.link_rows,
        input_polygon_rows - polygon_rows_count,
        assets.duplicate_image_rows,
        assets.duplicate_link_rows,
        assets.orphan_rows,
    )


__all__ = [
    "LEGACY_PUBLIC_ASSET_RELATIVE",
    "PUBLIC_ASSET_RELATIVE",
    "PUBLIC_IMAGE_RELATIVE",
    "PUBLIC_LINK_RELATIVE",
    "PUBLIC_MANIFEST_RELATIVE",
    "PUBLIC_POLYGON_RELATIVE",
    "PUBLIC_SCHEMA_VERSION",
    "PublicDatasetResult",
    "build_public_dataset",
    "public_polygon_schema",
    "validate_public_dataset",
]
