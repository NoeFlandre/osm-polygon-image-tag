import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from osm_polygon_image_tag.assets.schema import asset_schema
from osm_polygon_image_tag.core.errors import ImageTagPipelineError


class AssetStorageError(ImageTagPipelineError):
    """Raised when an asset shard is unsafe or violates its schema."""


@dataclass(frozen=True, slots=True)
class AssetWriteResult:
    row_count: int
    size_bytes: int


def validate_asset_parquet(path: Path, *, expected_rows: int | None = None) -> None:
    if path.is_symlink():
        raise AssetStorageError("asset Parquet must not be a symlink")
    try:
        parquet = pq.ParquetFile(path)
    except (OSError, pa.ArrowException) as error:
        raise AssetStorageError(f"unreadable asset Parquet: {path}") from error
    if not parquet.schema_arrow.equals(asset_schema(), check_metadata=True):
        raise AssetStorageError("asset Parquet schema or metadata does not match")
    if expected_rows is not None and parquet.metadata.num_rows != expected_rows:
        raise AssetStorageError("asset Parquet row count does not match")
    for row_group in range(parquet.metadata.num_row_groups):
        parquet.read_row_group(row_group, columns=["osm_id", "image_url"])


def _write_batches(
    rows: Iterable[Mapping[str, object]],
    path: Path,
    *,
    batch_size: int,
) -> int:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    schema = asset_schema()
    batch: list[Mapping[str, object]] = []
    row_count = 0
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


def write_asset_parquet(
    rows: Iterable[Mapping[str, object]],
    final_path: Path,
    *,
    batch_size: int = 4096,
) -> AssetWriteResult:
    if final_path.is_symlink():
        raise AssetStorageError("asset Parquet final path must not be a symlink")
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if final_path.parent.is_symlink():
        raise AssetStorageError("asset Parquet directory must not be a symlink")
    with tempfile.NamedTemporaryFile(
        prefix=f".{final_path.name}.",
        suffix=".tmp",
        dir=final_path.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        row_count = _write_batches(rows, temporary_path, batch_size=batch_size)
        validate_asset_parquet(temporary_path, expected_rows=row_count)
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
    return AssetWriteResult(row_count=row_count, size_bytes=final_path.stat().st_size)
