from collections import UserDict
from collections.abc import Mapping

import pytest

from osm_polygon_image_tag.resolvers.response import (
    as_integer,
    as_mapping,
    as_sequence,
    as_text,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"key": "value"}, {"key": "value"}),
        (UserDict({"key": "value"}), UserDict({"key": "value"})),
        ([], {}),
        ("value", {}),
        (1, {}),
        (None, {}),
    ],
)
def test_as_mapping(value: object, expected: Mapping[str, object]) -> None:
    result = as_mapping(value)

    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([1, "two"], [1, "two"]),
        ((1, "two"), (1, "two")),
        ("value", ()),
        ({"key": "value"}, ()),
        (range(2), ()),
        (1, ()),
        (None, ()),
    ],
)
def test_as_sequence(value: object, expected: list[object] | tuple[object, ...]) -> None:
    result = as_sequence(value)

    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("value", "value"),
        (" ", " "),
        ("", None),
        (1, None),
        (True, None),
        (None, None),
    ],
)
def test_as_text(value: object, expected: str | None) -> None:
    result = as_text(value)

    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1),
        (-1, -1),
        (0, 0),
        (True, None),
        (False, None),
        ("1", None),
        (1.0, None),
        (None, None),
    ],
)
def test_as_integer(value: object, expected: int | None) -> None:
    result = as_integer(value)

    assert result == expected
    assert type(result) is type(expected)
