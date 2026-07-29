import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from osm_polygon_image_tag.errors import ImageTagPipelineError
from osm_polygon_image_tag.schema import GEOPARQUET_VERSION, dataset_schema


class StorageError(ImageTagPipelineError):
    """Raised when a shard cannot be independently validated."""


@dataclass(frozen=True, slots=True)
class WriteResult:
    row_count: int
    size_bytes: int


def _schema_matches(actual: pa.Schema, expected: pa.Schema) -> bool:
    if actual.names != expected.names or actual.metadata != expected.metadata:
        return False
    for actual_field, expected_field in zip(actual, expected, strict=True):
        if actual_field.nullable != expected_field.nullable:
            return False
        if expected_field.name == "tags":
            if not (
                pa.types.is_map(actual_field.type)
                and actual_field.type.key_type == pa.string()
                and actual_field.type.item_type == pa.string()
            ):
                return False
        elif actual_field.type != expected_field.type:
            return False
    return True


def validate_geoparquet(path: Path) -> None:
    try:
        parquet = pq.ParquetFile(path)
    except (OSError, pa.ArrowException) as error:
        raise StorageError(f"unreadable Parquet file: {path}") from error
    expected = dataset_schema()
    if not _schema_matches(parquet.schema_arrow, expected):
        raise StorageError("Parquet schema or metadata does not match the dataset schema")
    metadata = parquet.schema_arrow.metadata
    if metadata is None or b"geo" not in metadata:
        raise StorageError("GeoParquet metadata is missing")
    try:
        geo = json.loads(metadata[b"geo"])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StorageError("GeoParquet metadata is malformed") from error
    geometry = geo.get("columns", {}).get("geometry", {})
    if (
        geo.get("version") != GEOPARQUET_VERSION
        or geo.get("primary_column") != "geometry"
        or geometry.get("encoding") != "WKB"
        or geometry.get("geometry_types") != ["Polygon", "MultiPolygon"]
    ):
        raise StorageError("GeoParquet geometry metadata does not match the contract")
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
            batch.append(row)
            if len(batch) == batch_size:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                row_count += len(batch)
                batch.clear()
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
            row_count += len(batch)
    return row_count


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
