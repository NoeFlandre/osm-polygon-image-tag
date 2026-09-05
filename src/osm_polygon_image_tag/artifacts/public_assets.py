"""Build unique public images and polygon-to-image relationships."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from osm_polygon_image_tag.artifacts.checkpoints import remove_checkpoint_files
from osm_polygon_image_tag.artifacts.public_asset_accumulator import (
    _ASSET_DEDUP_COLUMNS,  # noqa: F401 - compatibility import
    _Accumulator,
    _AssetBatch,  # noqa: F401 - compatibility import
    _AssetColumns,  # noqa: F401 - compatibility import
    _ColumnarAssetRow,  # noqa: F401 - compatibility import
    _digest,  # noqa: F401 - compatibility import
    _iter_batches,
    _prepare_batch_values,  # noqa: F401 - compatibility import
    _prepare_columnar_batch_values,  # noqa: F401 - compatibility import
    image_id,
    image_identity,
)
from osm_polygon_image_tag.artifacts.public_asset_checkpoint import (
    PUBLIC_ASSET_CHECKPOINT_FILENAME,
    PUBLIC_ASSET_CHECKPOINT_MAX_FILE_BYTES,  # noqa: F401
    PUBLIC_ASSET_CHECKPOINT_MIN_FREE_BYTES,  # noqa: F401
    PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE,
    PUBLIC_ASSET_DEDUP_CHECKPOINT_SCHEMA_VERSION,  # noqa: F401
    PUBLIC_ASSET_SQLITE_CACHE_KIB,  # noqa: F401
    PUBLIC_ASSET_SQLITE_MMAP_BYTES,  # noqa: F401
    PUBLIC_ASSET_SQLITE_PAGE_SIZE,  # noqa: F401
    _checkpoint_max_bytes,
    _cleanup_public_asset_checkpoints,
    _is_external_checkpoint,
    _prepare_checkpoint_paths,
    _remove_incompatible_checkpoint,  # noqa: F401 - compatibility import
    _remove_legacy_checkpoints,
    is_compatible_asset_checkpoint,  # noqa: F401 - compatibility import
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
from osm_polygon_image_tag.assets.schema import asset_schema  # noqa: F401 - compatibility import
from osm_polygon_image_tag.core.atomic import promote_temporary_file, temporary_file_path


@dataclass(frozen=True, slots=True)
class PublicAssetsResult:
    image_path: Path
    link_path: Path
    image_rows: int
    link_rows: int
    duplicate_image_rows: int
    duplicate_link_rows: int
    orphan_rows: int


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
    return PublicAssetsResult(
        image_path=image_path,
        link_path=link_path,
        image_rows=image_rows,
        link_rows=link_rows,
        duplicate_image_rows=duplicate_images,
        duplicate_link_rows=duplicate_links,
        orphan_rows=accumulator.orphan_rows,
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
    return PublicAssetsResult(
        image_path=image_path,
        link_path=link_path,
        image_rows=0,
        link_rows=0,
        duplicate_image_rows=0,
        duplicate_link_rows=0,
        orphan_rows=0,
    )


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
