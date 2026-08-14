"""Build the deduplicated, publishable view from resumable internal shards."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from osm_polygon_image_tag.artifacts.asset_inventory import verified_asset_manifests
from osm_polygon_image_tag.artifacts.manifest_inventory import verified_manifests
from osm_polygon_image_tag.artifacts.public_assets import (
    PublicAssetsResult,
    build_public_asset_tables,
    validate_public_image_parquet,
    validate_public_link_parquet,
)
from osm_polygon_image_tag.core.atomic import atomic_write_bytes
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

PUBLIC_SCHEMA_VERSION = 2
PUBLIC_POLYGON_RELATIVE = "public/polygons.parquet"
LEGACY_PUBLIC_ASSET_RELATIVE = "public/image_assets.parquet"
PUBLIC_IMAGE_RELATIVE = "public/images.parquet"
PUBLIC_LINK_RELATIVE = "public/polygon_images.parquet"
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


def _identity(row: Mapping[str, Any]) -> tuple[str, int]:
    return (str(row["osm_type"]), int(row["osm_id"]))


def _identity_sort(key: tuple[str, int]) -> tuple[str, int]:
    return key


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


def _polygon_is_newer(
    source: str,
    row: Mapping[str, Any],
    source_feature: str,
    current: tuple[str, dict[str, Any], set[str], str],
) -> bool:
    current_source, current_row, _sources, current_feature = current
    candidate_rank = _polygon_rank(row)
    current_rank = _polygon_rank(current_row)
    if candidate_rank != current_rank:
        return candidate_rank > current_rank
    return (source, source_feature, _stable_row_key(dict(row))) < (
        current_source,
        current_feature,
        _stable_row_key(current_row),
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


def _stable_row_key(row: dict[str, Any]) -> str:
    return json.dumps(_jsonable(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iter_rows(
    manifests: Sequence[tuple[Any, Path]], *, batch_size: int = 8192
) -> Iterator[dict[str, Any]]:
    for _manifest, output in manifests:
        for batch in pq.ParquetFile(output).iter_batches(batch_size=batch_size):
            yield from batch.to_pylist()


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


def _validate_public_polygon(path: Path, *, expected_rows: int | None = None) -> None:
    parquet = pq.ParquetFile(path)
    actual = parquet.schema_arrow
    expected = public_polygon_schema()
    if (
        actual.names != expected.names
        or actual.metadata != expected.metadata
        or any(
            actual_field.type != expected_field.type
            or actual_field.nullable != expected_field.nullable
            for actual_field, expected_field in zip(actual, expected, strict=True)
        )
    ):
        raise ValueError("public polygon Parquet schema does not match")
    if expected_rows is not None and parquet.metadata.num_rows != expected_rows:
        raise ValueError("public polygon row count does not match")


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
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("public_schema_version") != PUBLIC_SCHEMA_VERSION:
            raise ValueError("unsupported public dataset schema version")
        polygon_output = payload["polygon_output"]
        image_output = payload["image_output"]
        link_output = payload["link_output"]
        if polygon_output["size_bytes"] != polygon_path.stat().st_size:
            raise ValueError("public polygon size mismatch")
        if image_output["size_bytes"] != image_path.stat().st_size:
            raise ValueError("public image size mismatch")
        if link_output["size_bytes"] != link_path.stat().st_size:
            raise ValueError("public link size mismatch")
        if file_sha256(polygon_path) != polygon_output["sha256"]:
            raise ValueError("public polygon digest mismatch")
        if file_sha256(image_path) != image_output["sha256"]:
            raise ValueError("public image digest mismatch")
        if file_sha256(link_path) != link_output["sha256"]:
            raise ValueError("public link digest mismatch")
        _validate_public_polygon(polygon_path, expected_rows=int(polygon_output["row_count"]))
        validate_public_image_parquet(image_path, expected_rows=int(image_output["row_count"]))
        validate_public_link_parquet(link_path, expected_rows=int(link_output["row_count"]))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(str(error) or "public dataset artifacts are missing or invalid") from error
    return {
        PUBLIC_POLYGON_RELATIVE: str(polygon_output["sha256"]),
        PUBLIC_IMAGE_RELATIVE: str(image_output["sha256"]),
        PUBLIC_LINK_RELATIVE: str(link_output["sha256"]),
        PUBLIC_MANIFEST_RELATIVE: file_sha256(manifest_path),
    }


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
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("public_schema_version") != PUBLIC_SCHEMA_VERSION:
            return None
        if payload.get("polygon_inputs") != [m.output.sha256 for m, _ in polygon_manifests]:
            return None
        if payload.get("asset_inputs") != [m.output.sha256 for m, _ in asset_manifests]:
            return None
        if not polygon_path.is_file() or not image_path.is_file() or not link_path.is_file():
            return None
        polygon_manifest = _public_polygon_manifest(polygon_path, int(payload["polygon_rows"]))
        if polygon_manifest.output.sha256 != payload["polygon_output"]["sha256"]:
            return None
        if file_sha256(image_path) != payload["image_output"]["sha256"]:
            return None
        if file_sha256(link_path) != payload["link_output"]["sha256"]:
            return None
        validate_public_dataset(root)
        return PublicDatasetResult(
            polygon_path,
            image_path,
            link_path,
            manifest_path,
            polygon_manifest,
            polygon_manifest.output.row_count,
            int(payload["image_rows"]),
            int(payload["link_rows"]),
            int(payload["duplicate_polygon_rows"]),
            int(payload["duplicate_image_rows"]),
            int(payload["duplicate_link_rows"]),
            int(payload.get("orphan_asset_rows", 0)),
            reused=True,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def build_public_dataset(
    data_root: Path,
    *,
    manifests: Sequence[tuple[Any, Path]] | None = None,
    asset_manifests: Sequence[tuple[Any, Path]] | None = None,
) -> PublicDatasetResult:
    """Materialize a deterministic deduplicated view without touching inputs."""
    root = data_root.resolve()
    polygon_manifests = list(manifests) if manifests is not None else verified_manifests(root)
    source_assets = (
        list(asset_manifests) if asset_manifests is not None else verified_asset_manifests(root)
    )
    reused = _try_reuse(root, polygon_manifests, source_assets)
    if reused is not None:
        _remove_legacy_public_asset(root)
        return reused

    selected: dict[tuple[str, int], tuple[str, dict[str, Any], set[str], str]] = {}
    input_polygon_rows = 0
    for row in _iter_rows(polygon_manifests):
        input_polygon_rows += 1
        key = _identity(row)
        source = str(row["source_pbf"])
        source_feature = str(row.get("source_feature_id") or "")
        candidate = (source, row, {source}, source_feature)
        current = selected.get(key)
        if current is None:
            selected[key] = candidate
            continue
        current[2].add(source)
        if _polygon_is_newer(source, row, source_feature, current):
            selected[key] = (source, row, current[2], source_feature)

    def polygon_rows() -> Iterator[dict[str, Any]]:
        for key in sorted(selected, key=_identity_sort):
            _source, row, sources, _feature = selected[key]
            output = dict(row)
            output["source_pbfs"] = sorted(sources)
            yield output

    polygon_path = root / PUBLIC_POLYGON_RELATIVE
    polygon_rows_count = _write_polygon_rows(polygon_rows(), polygon_path)
    polygon_manifest = _public_polygon_manifest(polygon_path, polygon_rows_count)
    canonical_polygons: dict[tuple[str, int], dict[str, Any]] = {}
    for key, (source, row, sources, _feature) in selected.items():
        output = dict(row)
        output["source_pbf"] = source
        output["source_pbfs"] = sorted(sources)
        canonical_polygons[key] = output
    assets = build_public_asset_tables(root, source_assets, canonical_polygons)
    payload = _manifest_payload(
        polygon_manifests,
        source_assets,
        polygon_manifest,
        assets,
        polygon_rows=polygon_rows_count,
        image_rows=assets.image_rows,
        link_rows=assets.link_rows,
        duplicate_polygon_rows=input_polygon_rows - polygon_rows_count,
        duplicate_image_rows=assets.duplicate_image_rows,
        duplicate_link_rows=assets.duplicate_link_rows,
        orphan_asset_rows=assets.orphan_rows,
    )
    atomic_write_bytes(
        root / PUBLIC_MANIFEST_RELATIVE,
        (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
        prefix=".public-manifest.",
        suffix=".tmp",
        sync_directory=True,
    )
    _remove_legacy_public_asset(root)
    return PublicDatasetResult(
        polygon_path,
        assets.image_path,
        assets.link_path,
        root / PUBLIC_MANIFEST_RELATIVE,
        polygon_manifest,
        polygon_rows_count,
        assets.image_rows,
        assets.link_rows,
        input_polygon_rows - polygon_rows_count,
        assets.duplicate_image_rows,
        assets.duplicate_link_rows,
        assets.orphan_rows,
    )


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
