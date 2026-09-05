from osm_polygon_image_tag.artifacts.public_polygon_accumulator import (
    _advance_polygon_source_group,
)


def test_polygon_source_group_keeps_matching_group() -> None:
    groups = iter([("way", 1, "a"), ("way", 2, "b")])
    assert _advance_polygon_source_group(next(groups), groups, ("way", 1)) == (
        "way",
        1,
        "a",
    )
