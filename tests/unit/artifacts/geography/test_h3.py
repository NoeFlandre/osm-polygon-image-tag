"""Tests for H3 cell assignment and antimeridian-safe geometry."""

from __future__ import annotations

import math
from itertools import pairwise
from typing import cast

import pytest

from osm_polygon_image_tag.artifacts.geography.h3 import (
    DEFAULT_H3_RESOLUTION,
    aggregate_centroids_to_cells,
    assign_h3_cell,
    cell_rings,
    split_antimeridian,
)
from osm_polygon_image_tag.artifacts.geography.models import GeographicMapError


def test_default_h3_resolution_is_three() -> None:
    assert DEFAULT_H3_RESOLUTION == 3


def test_assign_h3_cell_returns_string_at_resolution_3() -> None:
    cell = assign_h3_cell(43.73, 7.42, resolution=3)
    assert isinstance(cell, str)
    assert cell.startswith("83")
    # Resolution 3 is encoded into the second nibble.
    assert int(cell[1], 16) == 3


def test_assign_h3_cell_matches_known_value_for_paris() -> None:
    assert assign_h3_cell(48.8566, 2.3522, resolution=3) == "831fb4fffffffff"


def test_aggregate_centroids_returns_sorted_cells_without_deduplicating() -> None:
    centroids = [(48.8566, 2.3522), (43.73, 7.42), (48.8566, 2.3522)]

    cells = aggregate_centroids_to_cells(centroids, resolution=3)

    assert cells == sorted(cells)
    assert cells.count("831fb4fffffffff") == 2


def test_assign_h3_cell_rejects_nan_latitude() -> None:
    with pytest.raises(GeographicMapError):
        assign_h3_cell(math.nan, 0.0, resolution=3)


def test_assign_h3_cell_rejects_nan_longitude() -> None:
    with pytest.raises(GeographicMapError):
        assign_h3_cell(0.0, math.nan, resolution=3)


def test_assign_h3_cell_rejects_infinite_latitude() -> None:
    with pytest.raises(GeographicMapError):
        assign_h3_cell(math.inf, 0.0, resolution=3)


def test_assign_h3_cell_rejects_out_of_range_latitude() -> None:
    with pytest.raises(GeographicMapError):
        assign_h3_cell(91.0, 0.0, resolution=3)


def test_assign_h3_cell_rejects_out_of_range_longitude() -> None:
    with pytest.raises(GeographicMapError):
        assign_h3_cell(0.0, 181.0, resolution=3)


def test_assign_h3_cell_rejects_invalid_resolution() -> None:
    with pytest.raises(GeographicMapError):
        assign_h3_cell(0.0, 0.0, resolution=99)


def test_assign_h3_cell_rejects_non_integer_resolution() -> None:
    with pytest.raises(GeographicMapError):
        assign_h3_cell(0.0, 0.0, resolution=cast(int, 3.0))


def test_assign_h3_cell_rejects_none() -> None:
    with pytest.raises(GeographicMapError):
        assign_h3_cell(cast(float, None), 0.0, resolution=3)


def test_assign_h3_cell_rejects_non_numeric() -> None:
    with pytest.raises(GeographicMapError):
        assign_h3_cell(cast(float, "not-a-number"), 0.0, resolution=3)


def test_split_antimeridian_polygon_split_into_two_rings() -> None:
    source = [
        (179.0, 65.0),
        (-179.0, 65.0),
        (-178.0, 64.0),
        (178.0, 64.0),
    ]
    rings = split_antimeridian(source)
    assert len(rings) == 2
    for ring in rings:
        assert len(ring) >= 3
        assert all(-180.0 <= lon <= 180.0 for lon, _ in ring)
        closed = [*ring, ring[0]]
        assert all(abs(right[0] - left[0]) <= 180.0 for left, right in pairwise(closed))


def test_split_antimeridian_preserves_non_crossing_polygon() -> None:
    source = [(1.0, 2.0), (2.0, 2.0), (2.0, 1.0), (1.0, 1.0)]
    assert split_antimeridian(source) == [source]


def test_cell_rings_returns_local_rings_for_antimeridian_cell() -> None:
    cell = assign_h3_cell(65.0, 179.5, resolution=3)
    rings = cell_rings(cell)
    assert rings
    for ring in rings:
        assert len(ring) >= 3
        assert all(-180.0 <= lon <= 180.0 for lon, _ in ring)
