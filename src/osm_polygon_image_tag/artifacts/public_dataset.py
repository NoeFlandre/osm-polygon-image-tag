"""Build the deduplicated, publishable view from resumable internal shards."""

from __future__ import annotations

import json
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
from osm_polygon_image_tag.core.atomic import atomic_write_bytes, promote_temporary_file
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
from osm_polygon_image_tag.core.serialization import canonical_json, canonical_json_bytes

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
        promote_temporary_file(temporary_path, path, sync_directory=True)
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
    _validate_public_polygon_schema(parquet)
    _validate_public_polygon_rows(parquet, expected_rows)


def _validate_public_polygon_schema(parquet: pq.ParquetFile) -> None:
    if not _public_polygon_schema_matches(parquet.schema_arrow, public_polygon_schema()):
        raise ValueError("public polygon Parquet schema does not match")


def _validate_public_polygon_rows(parquet: pq.ParquetFile, expected_rows: int | None) -> None:
    if expected_rows is not None and parquet.metadata.num_rows != expected_rows:
        raise ValueError("public polygon row count does not match")


def _public_polygon_schema_matches(actual: pa.Schema, expected: pa.Schema) -> bool:
    if actual.names != expected.names or actual.metadata != expected.metadata:
        return False
    return all(
        actual_field.type == expected_field.type
        and actual_field.nullable == expected_field.nullable
        for actual_field, expected_field in zip(actual, expected, strict=True)
    )


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
        payload = _read_public_manifest(manifest_path)
        outputs = _public_output_paths(payload, polygon_path, image_path, link_path)
        for label, path, output in outputs:
            _validate_public_output(label, path, output)
        _validate_public_parquet_files(payload, polygon_path, image_path, link_path)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(str(error) or "public dataset artifacts are missing or invalid") from error
    polygon_output = payload["polygon_output"]
    image_output = payload["image_output"]
    link_output = payload["link_output"]
    return {
        PUBLIC_POLYGON_RELATIVE: str(polygon_output["sha256"]),
        PUBLIC_IMAGE_RELATIVE: str(image_output["sha256"]),
        PUBLIC_LINK_RELATIVE: str(link_output["sha256"]),
        PUBLIC_MANIFEST_RELATIVE: file_sha256(manifest_path),
    }


def _read_public_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("public_schema_version") != PUBLIC_SCHEMA_VERSION:
        raise ValueError("unsupported public dataset schema version")
    return payload


def _public_output_paths(
    payload: Mapping[str, Any], polygon_path: Path, image_path: Path, link_path: Path
) -> tuple[tuple[str, Path, Mapping[str, Any]], ...]:
    return (
        ("polygon", polygon_path, payload["polygon_output"]),
        ("image", image_path, payload["image_output"]),
        ("link", link_path, payload["link_output"]),
    )


def _validate_public_output(label: str, path: Path, output: Mapping[str, Any]) -> None:
    if output["size_bytes"] != path.stat().st_size:
        raise ValueError(f"public {label} size mismatch")
    if file_sha256(path) != output["sha256"]:
        raise ValueError(f"public {label} digest mismatch")


def _validate_public_parquet_files(
    payload: Mapping[str, Any], polygon_path: Path, image_path: Path, link_path: Path
) -> None:
    _validate_public_polygon(
        polygon_path, expected_rows=int(payload["polygon_output"]["row_count"])
    )
    validate_public_image_parquet(
        image_path, expected_rows=int(payload["image_output"]["row_count"])
    )
    validate_public_link_parquet(link_path, expected_rows=int(payload["link_output"]["row_count"]))


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
        if not _manifest_polygon_output_matches(polygon_output, output, digest):
            return None
        row_count = int(polygon_output["row_count"])
        return _nonnegative_row_count(row_count)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _manifest_polygon_output_matches(
    polygon_output: Mapping[str, Any], output: Path, digest: str
) -> bool:
    return (
        polygon_output["sha256"] == digest
        and int(polygon_output["size_bytes"]) == output.stat().st_size
    )


def _nonnegative_row_count(row_count: int) -> int | None:
    return row_count if row_count >= 0 else None


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
        payload = _read_public_manifest(manifest_path)
        if not _reuse_sources_and_outputs_match(
            payload,
            polygon_manifests,
            asset_manifests,
            polygon_path,
            image_path,
            link_path,
        ):
            return None
        polygon_manifest = _reuse_polygon_manifest(payload, polygon_path)
        if polygon_manifest is None:
            return None
        if not _reuse_hashes_match(payload, image_path, link_path):
            return None
        validate_public_dataset(root)
        return _reuse_result(
            payload, polygon_path, image_path, link_path, manifest_path, polygon_manifest
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _public_outputs_exist(*paths: Path) -> bool:
    return all(path.is_file() and not path.is_symlink() for path in paths)


def _reuse_sources_and_outputs_match(
    payload: Mapping[str, Any],
    polygon_manifests: Sequence[tuple[Any, Path]],
    asset_manifests: Sequence[tuple[Any, Path]],
    polygon_path: Path,
    image_path: Path,
    link_path: Path,
) -> bool:
    return _reuse_inputs_match(
        payload, polygon_manifests, asset_manifests
    ) and _public_outputs_exist(polygon_path, image_path, link_path)


def _reuse_inputs_match(
    payload: Mapping[str, Any],
    polygon_manifests: Sequence[tuple[Any, Path]],
    asset_manifests: Sequence[tuple[Any, Path]],
) -> bool:
    return payload.get("polygon_inputs") == [m.output.sha256 for m, _ in polygon_manifests] and (
        payload.get("asset_inputs") == [m.output.sha256 for m, _ in asset_manifests]
    )


def _reuse_polygon_manifest(payload: Mapping[str, Any], polygon_path: Path) -> Manifest | None:
    polygon_manifest = _public_polygon_manifest(polygon_path, int(payload["polygon_rows"]))
    return (
        polygon_manifest
        if polygon_manifest.output.sha256 == payload["polygon_output"]["sha256"]
        else None
    )


def _reuse_hashes_match(payload: Mapping[str, Any], image_path: Path, link_path: Path) -> bool:
    return (
        file_sha256(image_path) == payload["image_output"]["sha256"]
        and file_sha256(link_path) == payload["link_output"]["sha256"]
    )


def _reuse_result(
    payload: Mapping[str, Any],
    polygon_path: Path,
    image_path: Path,
    link_path: Path,
    manifest_path: Path,
    polygon_manifest: Manifest,
) -> PublicDatasetResult:
    return PublicDatasetResult(
        polygon_path=polygon_path,
        image_path=image_path,
        link_path=link_path,
        manifest_path=manifest_path,
        polygon_manifest=polygon_manifest,
        polygon_rows=polygon_manifest.output.row_count,
        image_rows=int(payload["image_rows"]),
        link_rows=int(payload["link_rows"]),
        duplicate_polygon_rows=int(payload["duplicate_polygon_rows"]),
        duplicate_image_rows=int(payload["duplicate_image_rows"]),
        duplicate_link_rows=int(payload["duplicate_link_rows"]),
        orphan_asset_rows=int(payload.get("orphan_asset_rows", 0)),
        reused=True,
    )


def build_public_dataset(
    data_root: Path,
    *,
    manifests: Sequence[tuple[Any, Path]] | None = None,
    asset_manifests: Sequence[tuple[Any, Path]] | None = None,
    asset_checkpoint_root: Path | None = None,
) -> PublicDatasetResult:
    """Materialize a deterministic deduplicated view without touching inputs."""
    root = data_root.resolve()
    polygon_manifests = list(manifests) if manifests is not None else verified_manifests(root)
    source_assets = (
        list(asset_manifests) if asset_manifests is not None else verified_asset_manifests(root)
    )
    reused = _try_reuse(root, polygon_manifests, source_assets)
    if reused is not None:
        _cleanup_reused_public_dataset(root)
        return reused

    temporary_root, created_temporary_root = _prepare_public_build_root(root)
    database_path = root / PUBLIC_DEDUP_CHECKPOINT_RELATIVE
    polygon_path, polygon_rows_count, input_polygon_rows = _materialize_polygons(
        root, polygon_manifests, database_path
    )
    canonical_polygons = _canonical_polygon_index(polygon_path)
    polygon_manifest = _public_polygon_manifest(polygon_path, polygon_rows_count)
    assets = build_public_asset_tables(
        root,
        source_assets,
        canonical_polygons,
        polygon_fingerprint=polygon_manifest.output.sha256,
        checkpoint_root=asset_checkpoint_root,
    )
    result = _write_public_dataset(
        root,
        polygon_manifests,
        source_assets,
        polygon_manifest,
        assets,
        polygon_rows=polygon_rows_count,
        input_polygon_rows=input_polygon_rows,
    )
    _cleanup_public_build(root, temporary_root, created_temporary_root, database_path)
    return result


def _cleanup_reused_public_dataset(root: Path) -> None:
    remove_checkpoint_files(root / PUBLIC_DEDUP_CHECKPOINT_RELATIVE)
    remove_checkpoint_files(root / PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE)
    _remove_legacy_public_asset(root)


def _prepare_public_build_root(root: Path) -> tuple[Path, bool]:
    temporary_root = root / "tmp"
    created = not temporary_root.exists()
    temporary_root.mkdir(parents=True, exist_ok=True)
    return temporary_root, created


def _process_polygon_sources(
    accumulator: _PolygonAccumulator,
    polygon_manifests: Sequence[tuple[Any, Path]],
) -> None:
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


def _reusable_polygon_rows(
    root: Path, accumulator: _PolygonAccumulator, polygon_path: Path
) -> int | None:
    checkpoint = _polygon_output_checkpoint(accumulator, polygon_path)
    if checkpoint is None:
        return None
    recorded_digest, row_count = checkpoint
    try:
        return _validated_reusable_rows(root, polygon_path, recorded_digest, row_count)
    except (OSError, ValueError, pa.ArrowException):
        return None


def _polygon_output_checkpoint(
    accumulator: _PolygonAccumulator, polygon_path: Path
) -> tuple[str, int | None] | None:
    recorded_digest = accumulator.public_output_sha256()
    if not accumulator.all_sources_completed() or recorded_digest is None:
        return None
    if not polygon_path.is_file():
        return None
    return recorded_digest, accumulator.public_output_rows()


def _validated_reusable_rows(
    root: Path,
    polygon_path: Path,
    recorded_digest: str,
    row_count: int | None,
) -> int | None:
    row_count = _resolved_polygon_row_count(root, polygon_path, recorded_digest, row_count)
    _validate_public_polygon(polygon_path, expected_rows=row_count)
    return row_count if _polygon_digest_matches(polygon_path, recorded_digest) else None


def _resolved_polygon_row_count(
    root: Path, polygon_path: Path, recorded_digest: str, row_count: int | None
) -> int:
    if row_count is not None:
        return row_count
    recorded = _manifest_polygon_row_count(root, polygon_path, recorded_digest)
    if recorded is not None:
        return recorded
    return int(pq.ParquetFile(polygon_path).metadata.num_rows)


def _polygon_digest_matches(path: Path, recorded_digest: str) -> bool:
    return file_sha256(path) == recorded_digest


def _materialize_polygons(
    root: Path,
    polygon_manifests: Sequence[tuple[Any, Path]],
    database_path: Path,
) -> tuple[Path, int, int]:
    input_hashes = [manifest.output.sha256 for manifest, _ in polygon_manifests]
    accumulator = _PolygonAccumulator(database_path, input_hashes=input_hashes)
    polygon_path = root / PUBLIC_POLYGON_RELATIVE
    try:
        _process_polygon_sources(accumulator, polygon_manifests)
        input_polygon_rows = accumulator.input_rows
        polygon_rows_count = _reusable_polygon_rows(root, accumulator, polygon_path)
        if polygon_rows_count is None:
            polygon_rows_count = _write_polygon_rows(accumulator.rows(), polygon_path)
            accumulator.record_public_output(file_sha256(polygon_path), polygon_rows_count)
        return polygon_path, polygon_rows_count, input_polygon_rows
    finally:
        accumulator.close()


def _write_public_dataset(
    root: Path,
    polygon_manifests: Sequence[tuple[Any, Path]],
    source_assets: Sequence[tuple[Any, Path]],
    polygon_manifest: Manifest,
    assets: PublicAssetsResult,
    *,
    polygon_rows: int,
    input_polygon_rows: int,
) -> PublicDatasetResult:
    payload = _manifest_payload(
        polygon_manifests,
        source_assets,
        polygon_manifest,
        assets,
        polygon_rows=polygon_rows,
        image_rows=assets.image_rows,
        link_rows=assets.link_rows,
        duplicate_polygon_rows=input_polygon_rows - polygon_rows,
        duplicate_image_rows=assets.duplicate_image_rows,
        duplicate_link_rows=assets.duplicate_link_rows,
        orphan_asset_rows=assets.orphan_rows,
    )
    atomic_write_bytes(
        root / PUBLIC_MANIFEST_RELATIVE,
        canonical_json_bytes(payload, newline=True),
        prefix=".public-manifest.",
        suffix=".tmp",
        sync_directory=True,
    )
    return PublicDatasetResult(
        root / PUBLIC_POLYGON_RELATIVE,
        assets.image_path,
        assets.link_path,
        root / PUBLIC_MANIFEST_RELATIVE,
        polygon_manifest,
        polygon_rows,
        assets.image_rows,
        assets.link_rows,
        input_polygon_rows - polygon_rows,
        assets.duplicate_image_rows,
        assets.duplicate_link_rows,
        assets.orphan_rows,
    )


def _cleanup_public_build(
    root: Path, temporary_root: Path, created_temporary_root: bool, database_path: Path
) -> None:
    _remove_legacy_public_asset(root)
    remove_checkpoint_files(database_path)
    if created_temporary_root and not any(temporary_root.iterdir()):
        temporary_root.rmdir()


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
