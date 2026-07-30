"""Explicit deep verification for finalized asset shards."""

from dataclasses import dataclass
from pathlib import Path

from osm_polygon_image_tag.assets.manifest import (
    AssetManifestError,
    read_asset_manifest,
)
from osm_polygon_image_tag.assets.storage import (
    AssetStorageError,
    validate_asset_parquet,
)
from osm_polygon_image_tag.core.manifest import file_sha256


@dataclass(frozen=True, slots=True)
class AssetVerifyResult:
    checked: int
    valid: int
    invalid: int


def verify_assets(data_root: Path) -> AssetVerifyResult:
    root = data_root.resolve()
    paths = sorted((root / "asset-manifests").glob("*.assets.manifest.json"))
    valid = 0
    for path in paths:
        try:
            manifest = read_asset_manifest(path, data_root=root)
            output = root / manifest.output.relative_path
            if file_sha256(output) != manifest.output.sha256:
                raise ValueError("asset output digest mismatch")
            validate_asset_parquet(
                output,
                expected_rows=manifest.output.row_count,
            )
            valid += 1
        except (AssetManifestError, AssetStorageError, OSError, ValueError):
            continue
    return AssetVerifyResult(
        checked=len(paths),
        valid=valid,
        invalid=len(paths) - valid,
    )
