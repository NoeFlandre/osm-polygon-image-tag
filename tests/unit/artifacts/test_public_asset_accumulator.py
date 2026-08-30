from osm_polygon_image_tag.artifacts.public_asset_accumulator import _Accumulator


def test_asset_accumulator_is_owned_by_focused_module() -> None:
    assert _Accumulator.__module__ == ("osm_polygon_image_tag.artifacts.public_asset_accumulator")
