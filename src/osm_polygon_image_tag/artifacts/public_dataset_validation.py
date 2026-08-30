"""Validate and reuse the deterministic public dataset contract."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from osm_polygon_image_tag.artifacts.public_asset_schema import (
    validate_public_image_parquet,
    validate_public_link_parquet,
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

PUBLIC_SCHEMA_VERSION = 2
PUBLIC_POLYGON_RELATIVE = "public/polygons.parquet"
PUBLIC_IMAGE_RELATIVE = "public/images.parquet"
PUBLIC_LINK_RELATIVE = "public/polygon_images.parquet"
PUBLIC_MANIFEST_RELATIVE = "public/public-manifest.json"


def public_polygon_schema() -> pa.Schema:
    """Return the public polygon schema with complete source provenance."""
    fields = list(dataset_schema())
    fields.append(pa.field("source_pbfs", pa.list_(pa.string()), nullable=False))
    metadata = dict(dataset_schema().metadata or {})
    metadata[b"osm_polygon_image_tag_public_schema_version"] = str(PUBLIC_SCHEMA_VERSION).encode()
    return pa.schema(fields, metadata=metadata)


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


__all__ = [
    "PUBLIC_IMAGE_RELATIVE",
    "PUBLIC_LINK_RELATIVE",
    "PUBLIC_MANIFEST_RELATIVE",
    "PUBLIC_POLYGON_RELATIVE",
    "PUBLIC_SCHEMA_VERSION",
    "public_polygon_schema",
    "validate_public_dataset",
]
