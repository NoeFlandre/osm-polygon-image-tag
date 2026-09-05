import osm_polygon_image_tag.artifacts.public_asset_accumulator as accumulator_module
from osm_polygon_image_tag.artifacts.public_asset_rows import _AssetBatch, image_identity


def test_asset_row_transformation_preserves_compatibility_exports() -> None:
    assert image_identity.__module__ == "osm_polygon_image_tag.artifacts.public_asset_rows"
    assert accumulator_module._AssetBatch is _AssetBatch
    assert accumulator_module.image_identity is image_identity
