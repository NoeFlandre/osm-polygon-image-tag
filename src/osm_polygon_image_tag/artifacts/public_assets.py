"""Build unique public images and polygon-to-image relationships."""

from __future__ import annotations

import hashlib
import json
import pickle
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq

from osm_polygon_image_tag.artifacts.checkpoints import remove_checkpoint_files
from osm_polygon_image_tag.artifacts.public_asset_checkpoint import (
    PUBLIC_ASSET_CHECKPOINT_FILENAME,
    PUBLIC_ASSET_CHECKPOINT_MAX_FILE_BYTES,  # noqa: F401
    PUBLIC_ASSET_CHECKPOINT_MIN_FREE_BYTES,  # noqa: F401
    PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE,
    PUBLIC_ASSET_DEDUP_CHECKPOINT_SCHEMA_VERSION,
    PUBLIC_ASSET_SQLITE_CACHE_KIB,
    PUBLIC_ASSET_SQLITE_MMAP_BYTES,
    PUBLIC_ASSET_SQLITE_PAGE_SIZE,
    _checkpoint_max_bytes,
    _cleanup_public_asset_checkpoints,
    _is_external_checkpoint,
    _prepare_checkpoint_paths,
    _remove_incompatible_checkpoint,
    _remove_legacy_checkpoints,
    is_compatible_asset_checkpoint,
)
from osm_polygon_image_tag.artifacts.public_asset_schema import (
    PUBLIC_IMAGE_SCHEMA_VERSION,
    PUBLIC_LINK_SCHEMA_VERSION,
    public_image_schema,
    public_link_schema,
    validate_public_image_parquet,
    validate_public_link_parquet,
)
from osm_polygon_image_tag.assets.manifest import AssetManifest
from osm_polygon_image_tag.assets.schema import asset_schema
from osm_polygon_image_tag.core.atomic import promote_temporary_file, temporary_file_path
from osm_polygon_image_tag.core.serialization import canonical_json

# The private source-shard pointer is not needed to build public image/link rows.
_ASSET_DEDUP_COLUMNS = tuple(
    field.name for field in asset_schema() if field.name != "source_polygon_shard"
)


@dataclass(frozen=True, slots=True)
class PublicAssetsResult:
    image_path: Path
    link_path: Path
    image_rows: int
    link_rows: int
    duplicate_image_rows: int
    duplicate_link_rows: int
    orphan_rows: int


@dataclass(slots=True)
class _BatchValues:
    input_rows: int
    orphan_rows: int
    image_values: list[tuple[object, ...]]
    image_source_values: list[tuple[bytes, str]]
    link_values: list[tuple[bytes, object]]
    link_source_values: list[tuple[bytes, str]]
    link_version_values: list[tuple[bytes, int]]


@dataclass(frozen=True, slots=True)
class _AssetBatch:
    """Column-oriented asset rows for bounded, allocation-light processing."""

    columns: Mapping[str, Sequence[object]]
    row_count: int


@dataclass(frozen=True, slots=True)
class _AssetColumns:
    """Typed column references used by the allocation-light row loop."""

    osm_type: Sequence[object]
    osm_id: Sequence[object]
    osm_version: Sequence[object]
    source_pbf: Sequence[object]
    provider: Sequence[object]
    source_tag_key: Sequence[object]
    source_tag_value: Sequence[object]
    canonical_reference: Sequence[object]
    provider_asset_id: Sequence[object]
    asset_index: Sequence[object]
    relation_kind: Sequence[object]
    page_url: Sequence[object]
    image_url: Sequence[object]
    thumbnail_url: Sequence[object]
    image_url_expires_at: Sequence[object]
    mime_type: Sequence[object]
    width: Sequence[object]
    height: Sequence[object]
    license_id: Sequence[object]
    license_url: Sequence[object]
    author: Sequence[object]
    status: Sequence[object]
    reason: Sequence[object]
    category_truncated: Sequence[object]
    retry_after: Sequence[object]
    resolver_contract_version: Sequence[object]
    response_sha256: Sequence[object]

    @classmethod
    def from_batch(cls, batch: _AssetBatch) -> _AssetColumns:
        column = batch.columns.__getitem__
        return cls(
            column("osm_type"),
            column("osm_id"),
            column("osm_version"),
            column("source_pbf"),
            column("provider"),
            column("source_tag_key"),
            column("source_tag_value"),
            column("canonical_reference"),
            column("provider_asset_id"),
            column("asset_index"),
            column("relation_kind"),
            column("page_url"),
            column("image_url"),
            column("thumbnail_url"),
            column("image_url_expires_at"),
            column("mime_type"),
            column("width"),
            column("height"),
            column("license_id"),
            column("license_url"),
            column("author"),
            column("status"),
            column("reason"),
            column("category_truncated"),
            column("retry_after"),
            column("resolver_contract_version"),
            column("response_sha256"),
        )


class _ColumnarAssetRow(Mapping[str, object]):
    """Mapping view over one row of column-oriented asset values."""

    __slots__ = ("_columns", "index")

    def __init__(self, columns: _AssetColumns) -> None:
        self._columns = columns
        self.index = 0

    def __getitem__(self, name: str) -> object:
        try:
            column = getattr(self._columns, name)
        except AttributeError as error:
            raise KeyError(name) from error
        return column[self.index]

    def __iter__(self) -> Iterator[str]:
        return iter(_ASSET_DEDUP_COLUMNS)

    def __len__(self) -> int:
        return len(_ASSET_DEDUP_COLUMNS)


def _digest(value: object) -> bytes:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).digest()


def image_identity(row: Mapping[str, object]) -> tuple[str, str, str]:
    """Return a stable physical-image identity.

    A resolved image URL is preferred because it identifies the usable image
    itself. Provider IDs, canonical references, and page URLs are fallbacks for
    unresolved rows.
    """
    return _image_identity_values(
        row["provider"],
        row.get("image_url"),
        row.get("provider_asset_id"),
        row.get("canonical_reference"),
        row.get("page_url"),
    )


def _image_identity_values(
    provider_value: object,
    image_url: object,
    provider_asset_id: object,
    reference: object,
    page_url: object,
) -> tuple[str, str, str]:
    provider = str(provider_value)
    if image_url:
        return provider, "image_url", str(image_url)
    if provider_asset_id:
        return provider, "provider_asset_id", str(provider_asset_id)
    if reference:
        return provider, "canonical_reference", str(reference)
    return provider, "page_url", str(page_url or "")


def image_id(row: Mapping[str, object]) -> str:
    """Return an opaque, deterministic public image identifier."""
    return f"img_{_digest(image_identity(row)).hex()}"


def _quality_rank(row: Mapping[str, object]) -> int:
    """Prefer usable, resolved, stable, and richly described image rows."""
    return _quality_rank_values(
        row.get("image_url"),
        row.get("status"),
        row.get("image_url_expires_at"),
        row.get("width"),
        row.get("height"),
        row.get("license_id"),
        row.get("author"),
        row.get("category_truncated"),
    )


def _quality_rank_values(
    image_url: object,
    status: object,
    image_url_expires_at: object,
    width: object,
    height: object,
    license_id: object,
    author: object,
    category_truncated: object,
) -> int:
    """Rank scalar asset values without constructing a row mapping."""
    return sum(
        weight
        for present, weight in (
            (image_url is not None, 1_000_000),
            (status == "resolved", 100_000),
            (image_url_expires_at is None, 10_000),
            (width is not None, 1_000),
            (height is not None, 500),
            (license_id is not None, 100),
            (author is not None, 10),
            (not bool(category_truncated), 1),
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


def _iter_batches(output: Path, *, batch_size: int = 8192) -> Iterator[_AssetBatch]:
    for batch in pq.ParquetFile(output).iter_batches(
        columns=_ASSET_DEDUP_COLUMNS,
        batch_size=batch_size,
    ):
        yield _AssetBatch(
            {name: batch.column(name).to_pylist() for name in _ASSET_DEDUP_COLUMNS},
            batch.num_rows,
        )


def _prepare_batch_values(
    rows: Iterable[tuple[Mapping[str, object], Mapping[str, object] | None]],
) -> _BatchValues:
    values = _BatchValues(0, 0, [], [], [], [], [])
    for row, polygon in rows:
        values.input_rows += 1
        if polygon is None:
            values.orphan_rows += 1
            continue
        _append_batch_row(values, row, polygon)
    return values


def _prepare_columnar_batch_values(
    batch: _AssetBatch,
    canonical_polygons: Mapping[tuple[str, int], Mapping[str, object]],
) -> _BatchValues:
    values = _BatchValues(0, 0, [], [], [], [], [])
    columns = _AssetColumns.from_batch(batch)
    row = _ColumnarAssetRow(columns)
    for index in range(batch.row_count):
        values.input_rows += 1
        polygon = canonical_polygons.get(
            (str(columns.osm_type[index]), int(str(columns.osm_id[index])))
        )
        if polygon is None:
            values.orphan_rows += 1
            continue
        row.index = index
        _append_batch_row(values, row, polygon)
    return values


def _append_batch_row(
    values: _BatchValues,
    row: Mapping[str, object],
    polygon: Mapping[str, object],
) -> None:
    source_pbf = str(row["source_pbf"])
    identity = image_identity(row)
    image_key = _digest(identity)
    public_id = f"img_{image_key.hex()}"
    payload = _image_payload(row, public_id)
    values.image_values.append(
        (
            image_key,
            sqlite3.Binary(pickle.dumps(payload, protocol=5)),
            _quality_rank(row),
            canonical_json(payload),
        )
    )
    values.image_source_values.append((image_key, source_pbf))
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
    link_payload = _link_payload(row, polygon, public_id)
    values.link_values.append((link_key, sqlite3.Binary(pickle.dumps(link_payload, protocol=5))))
    values.link_source_values.append((link_key, source_pbf))
    version = row.get("osm_version")
    if version is not None:
        values.link_version_values.append((link_key, int(str(version))))


def _link_payload(
    row: Mapping[str, object], polygon: Mapping[str, object], public_id: str
) -> dict[str, object]:
    return {
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


def _deduplicate_batch_values(values: _BatchValues) -> None:
    best_images: dict[bytes, tuple[object, ...]] = {}
    for value in values.image_values:
        key = cast(bytes, value[0])
        previous = best_images.get(key)
        if previous is None or _image_value_wins(value, previous):
            best_images[key] = value
    values.image_values = list(best_images.values())
    values.image_source_values = _deduplicate_values(values.image_source_values, key_columns=(0, 1))
    values.link_values = _deduplicate_values(values.link_values, key_columns=(0,))
    values.link_source_values = _deduplicate_values(values.link_source_values, key_columns=(0, 1))
    values.link_version_values = _deduplicate_values(values.link_version_values, key_columns=(0, 1))
    for batch in (
        values.image_values,
        values.image_source_values,
        values.link_values,
        values.link_source_values,
        values.link_version_values,
    ):
        batch.sort(key=lambda value: value[0])


def _image_value_wins(value: tuple[object, ...], previous: tuple[object, ...]) -> bool:
    value_rank, previous_rank = cast(int, value[2]), cast(int, previous[2])
    return value_rank > previous_rank or (
        value_rank == previous_rank and cast(str, value[3]) < cast(str, previous[3])
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


def _write_parquet(rows: Iterable[Mapping[str, object]], path: Path, schema: pa.Schema) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary_file_path(
        path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as temporary_path:
        count = _write_parquet_file(rows, temporary_path, schema)
        _promote_parquet(temporary_path, path)
    return count


def _write_parquet_file(rows: Iterable[Mapping[str, object]], path: Path, schema: pa.Schema) -> int:
    count = 0
    with pq.ParquetWriter(
        path, schema, compression="zstd", use_dictionary=True, write_statistics=True
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
    return count


def _promote_parquet(temporary_path: Path, final_path: Path) -> None:
    promote_temporary_file(temporary_path, final_path, sync_directory=True)


def build_public_asset_tables(
    data_root: Path,
    manifests: Sequence[tuple[AssetManifest, Path]],
    canonical_polygons: Mapping[tuple[str, int], Mapping[str, object]],
    *,
    polygon_fingerprint: str | None = None,
    checkpoint_root: Path | None = None,
) -> PublicAssetsResult:
    """Materialize unique images and deduplicated relationship links."""
    root = data_root.resolve()
    image_path = root / "public/images.parquet"
    link_path = root / "public/polygon_images.parquet"
    if not manifests:
        return _empty_public_asset_tables(root, image_path, link_path, checkpoint_root)
    accumulator, cleanup_paths = _open_public_asset_accumulator(
        root,
        checkpoint_root,
        manifests,
        polygon_fingerprint=polygon_fingerprint,
    )
    succeeded = False
    try:
        _process_asset_sources(accumulator, manifests, canonical_polygons)
        image_rows, link_rows, duplicate_images, duplicate_links = _asset_counts(accumulator)
        _write_public_asset_outputs(accumulator, image_path, link_path)
        succeeded = True
    finally:
        accumulator.close()
        _cleanup_public_asset_checkpoints(cleanup_paths, succeeded)
    return _public_asset_result(
        image_path,
        link_path,
        image_rows,
        link_rows,
        duplicate_images,
        duplicate_links,
        accumulator.orphan_rows,
    )


def _open_public_asset_accumulator(
    root: Path,
    checkpoint_root: Path | None,
    manifests: Sequence[tuple[AssetManifest, Path]],
    *,
    polygon_fingerprint: str | None,
) -> tuple[_Accumulator, tuple[Path, ...]]:
    database_path, cleanup_paths = _prepare_checkpoint_paths(root, checkpoint_root)
    for path in cleanup_paths:
        _remove_legacy_checkpoints(path.parent, path)
    input_hashes = [manifest.output.sha256 for manifest, _ in manifests]
    external_checkpoint = _is_external_checkpoint(database_path, checkpoint_root)
    accumulator = _Accumulator(
        database_path,
        input_hashes=input_hashes,
        polygon_fingerprint=polygon_fingerprint,
        max_bytes=_checkpoint_max_bytes(database_path) if external_checkpoint else None,
    )
    return accumulator, cleanup_paths


def _empty_public_asset_tables(
    root: Path,
    image_path: Path,
    link_path: Path,
    checkpoint_root: Path | None,
) -> PublicAssetsResult:
    _checkpoint, cleanup_paths = _prepare_checkpoint_paths(root, checkpoint_root)
    for path in cleanup_paths:
        remove_checkpoint_files(path)
    _write_public_asset_outputs(None, image_path, link_path)
    return _public_asset_result(image_path, link_path, 0, 0, 0, 0, 0)


def _process_asset_sources(
    accumulator: _Accumulator,
    manifests: Sequence[tuple[AssetManifest, Path]],
    canonical_polygons: Mapping[tuple[str, int], Mapping[str, object]],
) -> None:
    for source_index, (manifest, output) in enumerate(manifests):
        source_sha256 = manifest.output.sha256
        if accumulator.source_completed(source_index, source_sha256):
            continue
        _process_asset_source(
            accumulator,
            manifest,
            output,
            source_index,
            source_sha256,
            canonical_polygons,
        )


def _process_asset_source(
    accumulator: _Accumulator,
    manifest: AssetManifest,
    output: Path,
    source_index: int,
    source_sha256: str,
    canonical_polygons: Mapping[tuple[str, int], Mapping[str, object]],
) -> None:
    accumulator.begin_source()
    source_rows = 0
    source_orphans_before = accumulator.orphan_rows
    try:
        for batch in _iter_batches(output):
            source_rows += batch.row_count
            accumulator.add_batch(batch, canonical_polygons)
        accumulator.complete_source(
            source_index,
            source_sha256,
            source_rows,
            accumulator.orphan_rows - source_orphans_before,
        )
    except BaseException:
        accumulator.rollback_source()
        raise


def _asset_counts(accumulator: _Accumulator) -> tuple[int, int, int, int]:
    image_rows, link_rows = accumulator.counts()
    matched_rows = accumulator.input_rows - accumulator.orphan_rows
    return image_rows, link_rows, matched_rows - image_rows, matched_rows - link_rows


def _write_public_asset_outputs(
    accumulator: _Accumulator | None,
    image_path: Path,
    link_path: Path,
) -> None:
    if accumulator is None:
        image_rows: Iterable[Mapping[str, object]] = ()
        link_rows: Iterable[Mapping[str, object]] = ()
    else:
        image_rows = accumulator.images()
        link_rows = accumulator.links()
    _write_parquet(image_rows, image_path, public_image_schema())
    _write_parquet(link_rows, link_path, public_link_schema())


def _public_asset_result(
    image_path: Path,
    link_path: Path,
    image_rows: int,
    link_rows: int,
    duplicate_image_rows: int,
    duplicate_link_rows: int,
    orphan_rows: int,
) -> PublicAssetsResult:
    return PublicAssetsResult(
        image_path=image_path,
        link_path=link_path,
        image_rows=image_rows,
        link_rows=link_rows,
        duplicate_image_rows=duplicate_image_rows,
        duplicate_link_rows=duplicate_link_rows,
        orphan_rows=orphan_rows,
    )


__all__ = [
    "PUBLIC_ASSET_CHECKPOINT_FILENAME",
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
