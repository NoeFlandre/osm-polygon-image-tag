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
    parquet = _open_asset_parquet(path)
    _validate_asset_schema(parquet)
    _validate_asset_row_count(parquet, expected_rows)
    _read_asset_groups(parquet)


def _open_asset_parquet(path: Path) -> pq.ParquetFile:
    try:
        return pq.ParquetFile(path)
    except (OSError, pa.ArrowException) as error:
        raise AssetStorageError(f"unreadable asset Parquet: {path}") from error


def _validate_asset_schema(parquet: pq.ParquetFile) -> None:
    if not parquet.schema_arrow.equals(asset_schema(), check_metadata=True):
        raise AssetStorageError("asset Parquet schema or metadata does not match")


def _validate_asset_row_count(parquet: pq.ParquetFile, expected_rows: int | None) -> None:
    if expected_rows is not None and parquet.metadata.num_rows != expected_rows:
        raise AssetStorageError("asset Parquet row count does not match")


def _read_asset_groups(parquet: pq.ParquetFile) -> None:
    for row_group in range(parquet.metadata.num_row_groups):
        parquet.read_row_group(row_group, columns=["osm_id", "image_url"])


class AtomicAssetWriter:
    """Incrementally write one bounded, atomically promoted asset shard."""

    def __init__(self, final_path: Path, *, batch_size: int = 4096) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if final_path.is_symlink():
            raise AssetStorageError("asset Parquet final path must not be a symlink")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if final_path.parent.is_symlink():
            raise AssetStorageError("asset Parquet directory must not be a symlink")
        self._final_path = final_path
        self._batch_size = batch_size
        with tempfile.NamedTemporaryFile(
            prefix=f".{final_path.name}.",
            suffix=".tmp",
            dir=final_path.parent,
            delete=False,
        ) as temporary:
            self._temporary_path = Path(temporary.name)
        self._schema = asset_schema()
        try:
            self._writer = pq.ParquetWriter(
                self._temporary_path,
                self._schema,
                compression="zstd",
                use_dictionary=True,
                write_statistics=True,
            )
        except BaseException:
            self._temporary_path.unlink(missing_ok=True)
            raise
        self._row_count = 0
        self.result: AssetWriteResult | None = None

    def write(self, rows: Iterable[Mapping[str, object]]) -> None:
        batch: list[Mapping[str, object]] = []
        for row in rows:
            batch.append(row)
            if len(batch) == self._batch_size:
                self._write_batch(batch)
                batch.clear()
        if batch:
            self._write_batch(batch)

    def _write_batch(self, batch: list[Mapping[str, object]]) -> None:
        self._writer.write_table(pa.Table.from_pylist(batch, schema=self._schema))
        self._row_count += len(batch)

    def __enter__(self) -> "AtomicAssetWriter":
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: object,
    ) -> None:
        try:
            self._writer.close()
            if exception_type is not None:
                return
            validate_asset_parquet(
                self._temporary_path,
                expected_rows=self._row_count,
            )
            with self._temporary_path.open("rb") as file_handle:
                os.fsync(file_handle.fileno())
            os.replace(self._temporary_path, self._final_path)
            directory_fd = os.open(self._final_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self.result = AssetWriteResult(
                row_count=self._row_count,
                size_bytes=self._final_path.stat().st_size,
            )
        finally:
            self._temporary_path.unlink(missing_ok=True)


def write_asset_parquet(
    rows: Iterable[Mapping[str, object]],
    final_path: Path,
    *,
    batch_size: int = 4096,
) -> AssetWriteResult:
    with AtomicAssetWriter(final_path, batch_size=batch_size) as writer:
        writer.write(rows)
    if writer.result is None:
        raise AssetStorageError("asset Parquet writer did not finalize")
    return writer.result
