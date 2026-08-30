from osm_polygon_image_tag.artifacts.public_polygon_accumulator import (
    _advance_polygon_source_group,
    _PolygonAccumulator,
)


def test_polygon_accumulator_is_owned_by_focused_module() -> None:
    assert _PolygonAccumulator.__module__ == (
        "osm_polygon_image_tag.artifacts.public_polygon_accumulator"
    )
    groups = iter([("way", 1, "a"), ("way", 2, "b")])
    assert _advance_polygon_source_group(next(groups), groups, ("way", 1)) == (
        "way",
        1,
        "a",
    )
