from pathlib import Path

from osm_polygon_image_tag.artifacts.asset_verify import verify_assets
from osm_polygon_image_tag.assets.manifest import (
    ASSET_MANIFEST_SCHEMA_VERSION,
    AssetManifest,
    AssetRunCounts,
    AssetSourceIdentity,
    ResolutionSnapshotIdentity,
    write_asset_manifest,
)
from osm_polygon_image_tag.assets.schema import (
    ASSET_SCHEMA_VERSION,
    RESOLVER_CONTRACT_VERSION,
)
from osm_polygon_image_tag.assets.storage import write_asset_parquet
from osm_polygon_image_tag.core.manifest import OutputIdentity, file_sha256


def test_deep_asset_verify_detects_same_size_corruption(tmp_path: Path) -> None:
    output = tmp_path / "assets" / "region.assets.parquet"
    write = write_asset_parquet([], output)
    write_asset_manifest(
        AssetManifest(
            ASSET_MANIFEST_SCHEMA_VERSION,
            ASSET_SCHEMA_VERSION,
            RESOLVER_CONTRACT_VERSION,
            AssetSourceIdentity("data/region.parquet", 1, "a" * 64, 0),
            ResolutionSnapshotIdentity(0, "b" * 64),
            OutputIdentity(
                "assets/region.assets.parquet",
                write.size_bytes,
                file_sha256(output),
                write.row_count,
            ),
            AssetRunCounts(0, {}, {}, 0, 0, 0),
        ),
        tmp_path / "asset-manifests" / "region.assets.manifest.json",
    )

    valid = verify_assets(tmp_path)
    content = bytearray(output.read_bytes())
    content[len(content) // 2] ^= 1
    output.write_bytes(content)
    corrupt = verify_assets(tmp_path)

    assert (valid.checked, valid.valid, valid.invalid) == (1, 1, 0)
    assert (corrupt.checked, corrupt.valid, corrupt.invalid) == (1, 0, 1)
