import osm_polygon_image_tag.artifacts.public_asset_accumulator as accumulator_module
from osm_polygon_image_tag.artifacts.public_asset_accumulator import _Accumulator
from osm_polygon_image_tag.artifacts.public_asset_rows import _AssetBatch, image_identity


def test_asset_accumulator_is_owned_by_focused_module() -> None:
    assert _Accumulator.__module__ == ("osm_polygon_image_tag.artifacts.public_asset_accumulator")


def test_asset_row_transformation_is_owned_by_focused_module() -> None:
    assert _AssetBatch.__module__ == "osm_polygon_image_tag.artifacts.public_asset_rows"
    assert image_identity.__module__ == "osm_polygon_image_tag.artifacts.public_asset_rows"
    assert accumulator_module._AssetBatch is _AssetBatch
    assert accumulator_module.image_identity is image_identity
