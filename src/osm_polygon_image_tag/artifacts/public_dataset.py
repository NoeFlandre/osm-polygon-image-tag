"""Build the deduplicated, publishable view from resumable internal shards."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from osm_polygon_image_tag.artifacts.asset_inventory import verified_asset_manifests
from osm_polygon_image_tag.artifacts.checkpoints import remove_checkpoint_files
from osm_polygon_image_tag.artifacts.manifest_inventory import verified_manifests
from osm_polygon_image_tag.artifacts.public_asset_checkpoint import (
    PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE,
)
from osm_polygon_image_tag.artifacts.public_asset_schema import (
    validate_public_image_parquet,
    validate_public_link_parquet,
)
from osm_polygon_image_tag.artifacts.public_assets import (
    PublicAssetsResult,
    build_public_asset_tables,
)
from osm_polygon_image_tag.artifacts.public_polygon_accumulator import (
    PUBLIC_DEDUP_CHECKPOINT_SCHEMA_VERSION,  # noqa: F401 - compatibility import
    _advance_polygon_source_group,  # noqa: F401 - compatibility import
    _PolygonAccumulator,
    _remove_incompatible_polygon_checkpoint,  # noqa: F401 - compatibility import
)
from osm_polygon_image_tag.core.atomic import (
    atomic_write_bytes,
    promote_temporary_file,
    temporary_file_path,
)
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
from osm_polygon_image_tag.core.serialization import canonical_json_bytes

PUBLIC_SCHEMA_VERSION = 2
PUBLIC_POLYGON_RELATIVE = "public/polygons.parquet"
LEGACY_PUBLIC_ASSET_RELATIVE = "public/image_assets.parquet"
PUBLIC_IMAGE_RELATIVE = "public/images.parquet"
PUBLIC_LINK_RELATIVE = "public/polygon_images.parquet"
PUBLIC_DEDUP_CHECKPOINT_RELATIVE = "tmp/.public-polygons.sqlite"
# Kept as an import-compatible alias for callers that only need the image file.
PUBLIC_ASSET_RELATIVE = PUBLIC_IMAGE_RELATIVE
PUBLIC_MANIFEST_RELATIVE = "public/public-manifest.json"


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


def _iter_source_batches(output: Path, *, batch_size: int = 8192) -> Iterator[list[dict[str, Any]]]:
    for batch in pq.ParquetFile(output).iter_batches(batch_size=batch_size):
        yield batch.to_pylist()


def _write_polygon_rows(
    rows: Iterable[dict[str, Any]], path: Path, *, batch_size: int = 4096
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = public_polygon_schema()
    count = 0
    with temporary_file_path(
        path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as temporary_path:
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
