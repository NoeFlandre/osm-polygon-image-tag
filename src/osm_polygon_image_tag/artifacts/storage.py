import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq

from osm_polygon_image_tag.core.contracts import PANORAMAX_VALUES_COLUMN
from osm_polygon_image_tag.core.errors import ImageTagPipelineError
from osm_polygon_image_tag.core.schema import GEOPARQUET_VERSION, dataset_schema


class StorageError(ImageTagPipelineError):
    """Raised when a shard cannot be independently validated."""


@dataclass(frozen=True, slots=True)
class WriteResult:
    row_count: int
    size_bytes: int


def _schema_matches(actual: pa.Schema, expected: pa.Schema) -> bool:
    if actual.names != expected.names or actual.metadata != expected.metadata:
        return False
    return all(
        actual_field.nullable == expected_field.nullable
        and actual_field.type == expected_field.type
        for actual_field, expected_field in zip(actual, expected, strict=True)
    )


def validate_geoparquet(path: Path) -> None:
    try:
        parquet = pq.ParquetFile(path)
    except (OSError, pa.ArrowException) as error:
        raise StorageError(f"unreadable Parquet file: {path}") from error
    expected = dataset_schema()
    if not _schema_matches(parquet.schema_arrow, expected):
        raise StorageError("Parquet schema or metadata does not match the dataset schema")
    _validate_geo_metadata(parquet.schema_arrow.metadata)
    _read_geometry_groups(parquet)


def _validate_geo_metadata(metadata: Mapping[bytes, bytes] | None) -> None:
    raw_geo = _geo_metadata_bytes(metadata)
    if raw_geo is None:
        raise StorageError("GeoParquet metadata is missing")
    geo = _parse_geo_metadata(raw_geo)
    if not _valid_geo_metadata(geo):
        raise StorageError("GeoParquet geometry metadata does not match the contract")


def _parse_geo_metadata(raw_geo: bytes) -> object:
    try:
        return json.loads(raw_geo)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StorageError("GeoParquet metadata is malformed") from error


def _geo_metadata_bytes(metadata: Mapping[bytes, bytes] | None) -> bytes | None:
    if metadata is None:
        return None
    return metadata.get(b"geo")


def _valid_geo_metadata(geo: object) -> bool:
    if not isinstance(geo, dict):
        return False
    columns = geo.get("columns")
    if not isinstance(columns, dict):
        return False
    geometry = columns.get("geometry")
    if not isinstance(geometry, dict):
        return False
    return _geo_fields_match(
        cast(Mapping[object, object], geo), cast(Mapping[object, object], geometry)
    )


def _geo_fields_match(geo: Mapping[object, object], geometry: Mapping[object, object]) -> bool:
    return _geo_version_matches(geo) and _geo_geometry_matches(geometry)


def _geo_version_matches(geo: Mapping[object, object]) -> bool:
    return geo.get("version") == GEOPARQUET_VERSION and geo.get("primary_column") == "geometry"


def _geo_geometry_matches(geometry: Mapping[object, object]) -> bool:
    return geometry.get("encoding") == "WKB" and geometry.get("geometry_types") == [
        "Polygon",
        "MultiPolygon",
    ]


def _read_geometry_groups(parquet: pq.ParquetFile) -> None:
    for row_group in range(parquet.metadata.num_row_groups):
        parquet.read_row_group(row_group, columns=["geometry"])


def _write_batches(
    rows: Iterable[Mapping[str, Any]],
    path: Path,
    *,
    batch_size: int,
) -> int:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    schema = dataset_schema()
    row_count = 0
    batch: list[Mapping[str, Any]] = []
    with pq.ParquetWriter(
        path,
        schema,
        compression="zstd",
        use_dictionary=True,
        write_statistics=True,
    ) as writer:
        for row in rows:
            batch.append(_normalize_storage_row(row))
            if len(batch) == batch_size:
                row_count += _write_storage_batch(writer, batch, schema)
        if batch:
            row_count += _write_storage_batch(writer, batch, schema)
    return row_count


def _normalize_storage_row(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    for name in ("tags", PANORAMAX_VALUES_COLUMN):
        value = normalized.get(name)
        if isinstance(value, Mapping):
            normalized[name] = [
                {"key": str(key), "value": str(item)}
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            ]
    return normalized


def _write_storage_batch(
    writer: pq.ParquetWriter,
    batch: list[Mapping[str, Any]],
    schema: pa.Schema,
) -> int:
    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
    size = len(batch)
    batch.clear()
    return size


def write_geoparquet(
    rows: Iterable[Mapping[str, Any]],
    final_path: Path,
    *,
    batch_size: int = 4096,
) -> WriteResult:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{final_path.name}.",
        suffix=".tmp",
        dir=final_path.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        row_count = _write_batches(rows, temporary_path, batch_size=batch_size)
        validate_geoparquet(temporary_path)
        with temporary_path.open("rb") as file_handle:
            os.fsync(file_handle.fileno())
        os.replace(temporary_path, final_path)
        directory_fd = os.open(final_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return WriteResult(row_count=row_count, size_bytes=final_path.stat().st_size)
