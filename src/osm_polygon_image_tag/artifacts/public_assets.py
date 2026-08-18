"""Build unique public images and polygon-to-image relationships."""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import sqlite3
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq

from osm_polygon_image_tag.artifacts.checkpoints import remove_checkpoint_files
from osm_polygon_image_tag.assets.manifest import AssetManifest

PUBLIC_IMAGE_SCHEMA_VERSION = 1
PUBLIC_LINK_SCHEMA_VERSION = 1
PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE = "tmp/.public-assets.sqlite"
PUBLIC_ASSET_DEDUP_CHECKPOINT_SCHEMA_VERSION = 1
# A larger bounded page cache reduces random B-tree I/O without unbounded RAM use.
PUBLIC_ASSET_SQLITE_CACHE_KIB = 131_072


@dataclass(frozen=True, slots=True)
class PublicAssetsResult:
    image_path: Path
    link_path: Path
    image_rows: int
    link_rows: int
    duplicate_image_rows: int
    duplicate_link_rows: int
    orphan_rows: int


def public_image_schema() -> pa.Schema:
    """Return the one-row-per-image public schema."""
    utc_timestamp = pa.timestamp("ms", tz="UTC")
    fields = [
        pa.field("image_id", pa.string(), nullable=False),
        pa.field("provider", pa.string(), nullable=False),
        pa.field("canonical_reference", pa.string(), nullable=False),
        pa.field("provider_asset_id", pa.string()),
        pa.field("page_url", pa.string()),
        pa.field("image_url", pa.string()),
        pa.field("thumbnail_url", pa.string()),
        pa.field("image_url_expires_at", utc_timestamp),
        pa.field("mime_type", pa.string()),
        pa.field("width", pa.int32()),
        pa.field("height", pa.int32()),
        pa.field("license_id", pa.string()),
        pa.field("license_url", pa.string()),
        pa.field("author", pa.string()),
        pa.field("status", pa.string(), nullable=False),
        pa.field("reason", pa.string()),
        pa.field("category_truncated", pa.bool_(), nullable=False),
        pa.field("retry_after", utc_timestamp),
        pa.field("resolver_contract_version", pa.int32(), nullable=False),
        pa.field("response_sha256", pa.string()),
        pa.field("source_pbfs", pa.list_(pa.field("element", pa.string())), nullable=False),
    ]
    return pa.schema(
        fields,
        metadata={
            b"osm_polygon_image_tag_public_image_schema_version": str(
                PUBLIC_IMAGE_SCHEMA_VERSION
            ).encode()
        },
    )


def public_link_schema() -> pa.Schema:
    """Return the many-to-many polygon/image relationship schema."""
    fields = [
        pa.field("osm_type", pa.string(), nullable=False),
        pa.field("osm_id", pa.int64(), nullable=False),
        pa.field("osm_version", pa.int32()),
        pa.field("image_id", pa.string(), nullable=False),
        pa.field("provider", pa.string(), nullable=False),
        pa.field("source_tag_key", pa.string(), nullable=False),
        pa.field("source_tag_value", pa.string(), nullable=False),
        pa.field("canonical_reference", pa.string(), nullable=False),
        pa.field("asset_index", pa.int32(), nullable=False),
        pa.field("relation_kind", pa.string(), nullable=False),
        pa.field("source_pbfs", pa.list_(pa.field("element", pa.string())), nullable=False),
        pa.field(
            "observed_osm_versions",
            pa.list_(pa.field("element", pa.int32())),
            nullable=False,
        ),
    ]
    return pa.schema(
        fields,
        metadata={
            b"osm_polygon_image_tag_public_link_schema_version": str(
                PUBLIC_LINK_SCHEMA_VERSION
            ).encode()
        },
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


def _stable_json(value: object) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> bytes:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).digest()


def image_identity(row: Mapping[str, object]) -> tuple[str, str, str]:
    """Return a stable physical-image identity.

    A resolved image URL is preferred because it identifies the usable image
    itself. Provider IDs, canonical references, and page URLs are fallbacks for
    unresolved rows.
    """
    provider = str(row["provider"])
    image_url = row.get("image_url")
    if image_url:
        return provider, "image_url", str(image_url)
    provider_asset_id = row.get("provider_asset_id")
    if provider_asset_id:
        return provider, "provider_asset_id", str(provider_asset_id)
    reference = row.get("canonical_reference")
    if reference:
        return provider, "canonical_reference", str(reference)
    return provider, "page_url", str(row.get("page_url") or "")


def image_id(row: Mapping[str, object]) -> str:
    """Return an opaque, deterministic public image identifier."""
    return f"img_{_digest(image_identity(row)).hex()}"


def _quality_rank(row: Mapping[str, object]) -> int:
    """Prefer usable, resolved, stable, and richly described image rows."""
    return sum(
        weight
        for present, weight in (
            (row.get("image_url") is not None, 1_000_000),
            (row.get("status") == "resolved", 100_000),
            (row.get("image_url_expires_at") is None, 10_000),
            (row.get("width") is not None, 1_000),
            (row.get("height") is not None, 500),
            (row.get("license_id") is not None, 100),
            (row.get("author") is not None, 10),
            (not bool(row.get("category_truncated")), 1),
        )
        if present
    )


def _image_payload(row: Mapping[str, object], public_id: str) -> dict[str, object]:
    return {
        "image_id": public_id,
        "provider": row["provider"],
        "canonical_reference": row["canonical_reference"],
        "provider_asset_id": row.get("provider_asset_id"),
        "page_url": row.get("page_url"),
        "image_url": row.get("image_url"),
        "thumbnail_url": row.get("thumbnail_url"),
        "image_url_expires_at": row.get("image_url_expires_at"),
        "mime_type": row.get("mime_type"),
        "width": row.get("width"),
        "height": row.get("height"),
        "license_id": row.get("license_id"),
        "license_url": row.get("license_url"),
        "author": row.get("author"),
        "status": row["status"],
        "reason": row.get("reason"),
        "category_truncated": bool(row["category_truncated"]),
        "retry_after": row.get("retry_after"),
        "resolver_contract_version": row["resolver_contract_version"],
        "response_sha256": row.get("response_sha256"),
    }


def _deduplicate_values[ValueTuple: tuple[Any, ...]](
    values: Sequence[ValueTuple], *, key_columns: tuple[int, ...]
) -> list[ValueTuple]:
    """Keep the first value for each bounded-batch index key."""
    seen: set[tuple[object, ...]] = set()
    unique: list[ValueTuple] = []
    for value in values:
        key = tuple(
            cast(bytes, value[index]) if index == 0 else value[index] for index in key_columns
        )
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


def _iter_batches(output: Path, *, batch_size: int = 8192) -> Iterator[list[dict[str, Any]]]:
    for batch in pq.ParquetFile(output).iter_batches(batch_size=batch_size):
        yield batch.to_pylist()


def _remove_legacy_checkpoints(temporary_root: Path, current: Path) -> None:
    for path in temporary_root.glob(".public-assets.*.sqlite*"):
        if path != current:
            path.unlink(missing_ok=True)


class _Accumulator:
    """Bounded-memory SQLite accumulator for public image relationships."""

    def __init__(
        self,
        path: Path,
        *,
        input_hashes: Sequence[str] | None = None,
        polygon_fingerprint: str | None = None,
    ) -> None:
        self.path = path
        self.input_hashes = tuple(input_hashes) if input_hashes is not None else None
        self.polygon_fingerprint = polygon_fingerprint or ""
        self._transaction_input_rows = 0
        self._transaction_orphan_rows = 0
        if (
            self.input_hashes is not None
            and path.is_file()
            and not self._is_compatible_checkpoint(
                path, self.input_hashes, self.polygon_fingerprint
            )
        ):
            remove_checkpoint_files(path)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute(f"PRAGMA cache_size=-{PUBLIC_ASSET_SQLITE_CACHE_KIB}")
        self.connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        self.connection.executescript(
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
        if self.input_hashes is None:
            self.input_rows = 0
            self.orphan_rows = 0
        else:
            self.connection.execute(
                "INSERT OR IGNORE INTO checkpoint_metadata(key, value) VALUES (?, ?)",
                ("schema_version", str(PUBLIC_ASSET_DEDUP_CHECKPOINT_SCHEMA_VERSION)),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO checkpoint_metadata(key, value) VALUES (?, ?)",
                ("input_hashes", json.dumps(self.input_hashes, separators=(",", ":"))),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO checkpoint_metadata(key, value) VALUES (?, ?)",
                ("polygon_fingerprint", self.polygon_fingerprint),
            )
            self.connection.commit()
            self.input_rows, self.orphan_rows = self._completed_counts()

    @staticmethod
    def _is_compatible_checkpoint(
        path: Path,
        input_hashes: Sequence[str],
        polygon_fingerprint: str,
    ) -> bool:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(path)
            metadata = dict(
                connection.execute("SELECT key, value FROM checkpoint_metadata").fetchall()
            )
            if metadata.get("schema_version") != str(PUBLIC_ASSET_DEDUP_CHECKPOINT_SCHEMA_VERSION):
                return False
            if json.loads(metadata["input_hashes"]) != list(input_hashes):
                return False
            if metadata.get("polygon_fingerprint") != polygon_fingerprint:
                return False
            for source_index, source_sha256, row_count, orphan_count in connection.execute(
                "SELECT source_index, source_sha256, row_count, orphan_count "
                "FROM checkpoint_sources"
            ):
                if (
                    source_index < 0
                    or source_index >= len(input_hashes)
                    or input_hashes[source_index] != source_sha256
                    or row_count < 0
                    or orphan_count < 0
                    or orphan_count > row_count
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
        materialized = list(rows)
        self.input_rows += len(materialized)
        self._transaction_input_rows += len(materialized)
        image_values: list[tuple[object, ...]] = []
        image_source_values: list[tuple[bytes, str]] = []
        link_values: list[tuple[bytes, object]] = []
        link_source_values: list[tuple[bytes, str]] = []
        link_version_values: list[tuple[bytes, int]] = []
        for row, polygon in materialized:
            if polygon is None:
                self.orphan_rows += 1
                self._transaction_orphan_rows += 1
                continue
            source_pbf = str(row["source_pbf"])
            identity = image_identity(row)
            image_key = _digest(identity)
            public_id = f"img_{image_key.hex()}"
            payload = _image_payload(row, public_id)
            image_values.append(
                (
                    image_key,
                    sqlite3.Binary(pickle.dumps(payload, protocol=5)),
                    _quality_rank(row),
                    _stable_json(payload),
                )
            )
            image_source_values.append((image_key, source_pbf))

            polygon_key = (str(polygon["osm_type"]), int(str(polygon["osm_id"])))
            link_identity = (
                polygon_key,
                identity,
                row["source_tag_key"],
                row["source_tag_value"],
                row["canonical_reference"],
                row["asset_index"],
                row["relation_kind"],
            )
            link_key = _digest(link_identity)
            link_payload = {
                "osm_type": polygon["osm_type"],
                "osm_id": polygon["osm_id"],
                "osm_version": polygon.get("osm_version"),
                "image_id": public_id,
                "provider": row["provider"],
                "source_tag_key": row["source_tag_key"],
                "source_tag_value": row["source_tag_value"],
                "canonical_reference": row["canonical_reference"],
                "asset_index": row["asset_index"],
                "relation_kind": row["relation_kind"],
            }
            link_values.append((link_key, sqlite3.Binary(pickle.dumps(link_payload, protocol=5))))
            link_source_values.append((link_key, source_pbf))
            version = row.get("osm_version")
            if version is not None:
                link_version_values.append((link_key, int(str(version))))

        best_images: dict[bytes, tuple[object, ...]] = {}
        for value in image_values:
            key = cast(bytes, value[0])
            previous = best_images.get(key)
            if (
                previous is None
                or cast(int, value[2]) > cast(int, previous[2])
                or (
                    cast(int, value[2]) == cast(int, previous[2])
                    and cast(str, value[3]) < cast(str, previous[3])
                )
            ):
                best_images[key] = value
        image_values = list(best_images.values())
        image_source_values = _deduplicate_values(image_source_values, key_columns=(0, 1))
        link_values = _deduplicate_values(link_values, key_columns=(0,))
        link_source_values = _deduplicate_values(link_source_values, key_columns=(0, 1))
        link_version_values = _deduplicate_values(link_version_values, key_columns=(0, 1))

        # Hash-derived keys arrive in source order, which is effectively random
        # for these B-trees.  Sorting each bounded batch makes writes mostly
        # sequential without changing any winner or provenance semantics.
        for values in (
            image_values,
            image_source_values,
            link_values,
            link_source_values,
            link_version_values,
        ):
            values.sort(key=lambda value: value[0])

        self.connection.executemany(
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
            image_values,
        )
        self.connection.executemany(
            "INSERT OR IGNORE INTO image_sources VALUES (?, ?)", image_source_values
        )
        self.connection.executemany("INSERT OR IGNORE INTO links VALUES (?, ?)", link_values)
        self.connection.executemany(
            "INSERT OR IGNORE INTO link_sources VALUES (?, ?)", link_source_values
        )
        self.connection.executemany(
            "INSERT OR IGNORE INTO link_versions VALUES (?, ?)", link_version_values
        )

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


def _write_parquet(rows: Iterable[Mapping[str, object]], path: Path, schema: pa.Schema) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    count = 0
    try:
        with pq.ParquetWriter(
            temporary_path, schema, compression="zstd", use_dictionary=True, write_statistics=True
        ) as writer:
            batch: list[Mapping[str, object]] = []
            for row in rows:
                batch.append(row)
                if len(batch) == 4096:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                    count += len(batch)
                    batch.clear()
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                count += len(batch)
            if count == 0:
                writer.write_table(pa.Table.from_pylist([], schema=schema))
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


def validate_public_image_parquet(path: Path, *, expected_rows: int | None = None) -> None:
    try:
        parquet = pq.ParquetFile(path)
    except (OSError, pa.ArrowException) as error:
        raise ValueError("public image Parquet is invalid") from error
    if not parquet.schema_arrow.equals(public_image_schema(), check_metadata=True):
        raise ValueError("public image Parquet schema does not match")
    if expected_rows is not None and parquet.metadata.num_rows != expected_rows:
        raise ValueError("public image row count does not match")


def validate_public_link_parquet(path: Path, *, expected_rows: int | None = None) -> None:
    try:
        parquet = pq.ParquetFile(path)
    except (OSError, pa.ArrowException) as error:
        raise ValueError("public link Parquet is invalid") from error
    if not parquet.schema_arrow.equals(public_link_schema(), check_metadata=True):
        raise ValueError("public link Parquet schema does not match")
    if expected_rows is not None and parquet.metadata.num_rows != expected_rows:
        raise ValueError("public link row count does not match")


def build_public_asset_tables(
    data_root: Path,
    manifests: Sequence[tuple[AssetManifest, Path]],
    canonical_polygons: Mapping[tuple[str, int], Mapping[str, object]],
    *,
    polygon_fingerprint: str | None = None,
) -> PublicAssetsResult:
    """Materialize unique images and deduplicated relationship links."""
    root = data_root.resolve()
    image_path = root / "public/images.parquet"
    link_path = root / "public/polygon_images.parquet"
    if not manifests:
        remove_checkpoint_files(root / PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE)
        _write_parquet([], image_path, public_image_schema())
        _write_parquet([], link_path, public_link_schema())
        return PublicAssetsResult(image_path, link_path, 0, 0, 0, 0, 0)
    temporary_root = root / "tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    database_path = root / PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE
    _remove_legacy_checkpoints(temporary_root, database_path)
    input_hashes = [manifest.output.sha256 for manifest, _ in manifests]
    accumulator = _Accumulator(
        database_path,
        input_hashes=input_hashes,
        polygon_fingerprint=polygon_fingerprint,
    )
    succeeded = False
    try:
        for source_index, (manifest, output) in enumerate(manifests):
            source_sha256 = manifest.output.sha256
            if accumulator.source_completed(source_index, source_sha256):
                continue
            accumulator.begin_source()
            source_rows = 0
            source_orphans_before = accumulator.orphan_rows
            try:
                for batch in _iter_batches(output):
                    source_rows += len(batch)
                    accumulator.add_many(
                        (
                            row,
                            canonical_polygons.get((str(row["osm_type"]), int(row["osm_id"]))),
                        )
                        for row in batch
                    )
                accumulator.complete_source(
                    source_index,
                    source_sha256,
                    source_rows,
                    accumulator.orphan_rows - source_orphans_before,
                )
            except BaseException:
                accumulator.rollback_source()
                raise
        image_rows, link_rows = accumulator.counts()
        matched_rows = accumulator.input_rows - accumulator.orphan_rows
        duplicate_images = matched_rows - image_rows
        duplicate_links = matched_rows - link_rows
        _write_parquet(accumulator.images(), image_path, public_image_schema())
        _write_parquet(accumulator.links(), link_path, public_link_schema())
        succeeded = True
    finally:
        accumulator.close()
        if succeeded:
            remove_checkpoint_files(database_path)
    return PublicAssetsResult(
        image_path=image_path,
        link_path=link_path,
        image_rows=image_rows,
        link_rows=link_rows,
        duplicate_image_rows=duplicate_images,
        duplicate_link_rows=duplicate_links,
        orphan_rows=accumulator.orphan_rows,
    )


__all__ = [
    "PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE",
    "PUBLIC_IMAGE_SCHEMA_VERSION",
    "PUBLIC_LINK_SCHEMA_VERSION",
    "PublicAssetsResult",
    "build_public_asset_tables",
    "image_id",
    "image_identity",
    "public_image_schema",
    "public_link_schema",
    "validate_public_image_parquet",
    "validate_public_link_parquet",
]
