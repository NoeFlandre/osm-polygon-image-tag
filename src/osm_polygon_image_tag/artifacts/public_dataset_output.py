"""Assemble and persist the deterministic public dataset outputs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from osm_polygon_image_tag.artifacts.public_assets import PublicAssetsResult
from osm_polygon_image_tag.artifacts.public_dataset_validation import (
    PUBLIC_MANIFEST_RELATIVE,
    PUBLIC_POLYGON_RELATIVE,
    PUBLIC_SCHEMA_VERSION,
)
from osm_polygon_image_tag.core.atomic import atomic_write_bytes
from osm_polygon_image_tag.core.manifest import Manifest, file_sha256
from osm_polygon_image_tag.core.serialization import canonical_json_bytes


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
