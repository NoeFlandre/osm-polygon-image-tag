"""Tests for normalized polygon reference reads."""

import pytest

from osm_polygon_image_tag.assets.polygon_input import _panoramax_pairs


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([{"key": "panoramax:0", "value": "picture"}], (("panoramax:0", "picture"),)),
        ([("panoramax:1", "next")], (("panoramax:1", "next"),)),
        ([["panoramax:2", "last"]], (("panoramax:2", "last"),)),
        ([{"key": "missing-value"}], (("missing-value", None),)),
        ([["too", "many", "values"]], ()),
        (["not-a-pair"], ()),
        ({"panoramax:0": "picture"}, (("panoramax:0", "picture"),)),
        ([{"key": "panoramax:0", "value": "picture"}, ["bad"]], (("panoramax:0", "picture"),)),
        ("not-a-pair-list", ()),
        (None, ()),
    ],
)
def test_panoramax_pairs_normalizes_supported_container_shapes(
    value: object, expected: tuple[tuple[object, object], ...]
) -> None:
    assert _panoramax_pairs(value) == expected
