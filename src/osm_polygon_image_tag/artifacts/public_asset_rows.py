"""Transform asset rows into deterministic, SQLite-ready batch values."""

from __future__ import annotations

import hashlib
import pickle
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq

from osm_polygon_image_tag.assets.schema import asset_schema
from osm_polygon_image_tag.core.serialization import canonical_json

# The private source-shard pointer is not needed to build public image/link rows.
_ASSET_DEDUP_COLUMNS = tuple(
    field.name for field in asset_schema() if field.name != "source_polygon_shard"
)


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
