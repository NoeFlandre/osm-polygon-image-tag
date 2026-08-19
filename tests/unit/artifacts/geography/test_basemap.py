"""Tests for the bundled Natural Earth basemap loader."""

from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

from osm_polygon_image_tag.artifacts.geography.basemap import (
    BUNDLED_BASEMAP_FILENAME,
    _read_features,
    draw_landmasses,
    load_land_basemap,
)


def test_bundled_basemap_filename_is_stable() -> None:
    assert BUNDLED_BASEMAP_FILENAME == "ne_110m_land.geojson"


def test_load_land_basemap_returns_none_for_missing_directory(tmp_path: Path) -> None:
    assert load_land_basemap(tmp_path) is None


def test_load_land_basemap_returns_none_for_empty_file(tmp_path: Path) -> None:
    (tmp_path / BUNDLED_BASEMAP_FILENAME).write_bytes(b"")
    assert load_land_basemap(tmp_path) is None


def test_load_land_basemap_returns_features_for_well_formed_geojson(tmp_path: Path) -> None:
    geojson = tmp_path / BUNDLED_BASEMAP_FILENAME
    geojson.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature",'
        '"geometry":{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,1],[0,0]]]},'
        '"properties":{}}]}'
    )

    features = load_land_basemap(tmp_path)
    assert features is not None
    assert len(features) == 1
    assert features[0]["geometry"]["type"] == "Polygon"


def test_load_land_basemap_returns_none_for_corrupt_geojson(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    geojson = tmp_path / BUNDLED_BASEMAP_FILENAME
    geojson.write_text("{not valid json")

    caplog.set_level("WARNING")
    assert load_land_basemap(tmp_path) is None
    # Renderer should not crash; basemap loader must surface a warning.
    assert any("land" in r.getMessage().lower() for r in caplog.records)


def test_read_features_returns_only_feature_collection_members(tmp_path: Path) -> None:
    source = tmp_path / "land.geojson"
    source.write_text('{"features": [{"type": "Feature"}]}')

    assert _read_features(source, missing_message=None, label="cached") == [{"type": "Feature"}]


def test_bundled_basemap_is_package_data() -> None:
    """The Natural Earth GeoJSON must be installed as a package data file."""
    from importlib.resources import files

    resource = files("osm_polygon_image_tag").joinpath(
        "artifacts/geography/_data/ne_110m_land.geojson"
    )
    assert resource.is_file()
    assert len(resource.read_bytes()) > 0


def test_basemap_loader_does_not_fetch_over_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("network call attempted")

    monkeypatch.setattr(urllib.request, "urlretrieve", fail)
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    # Loading from a fresh directory should not perform any network call.
    assert load_land_basemap(tmp_path) is None


def test_draw_landmasses_handles_polygon_multipolygon_and_malformed_features() -> None:
    class Axes:
        def __init__(self) -> None:
            self.patches: list[object] = []

        def add_patch(self, patch: object) -> None:
            self.patches.append(patch)

    ring = [[0, 0], [1, 0], [1, 1], [0, 0]]
    axes = Axes()
    draw_landmasses(
        axes,
        [
            None,
            {"geometry": None},
            {"geometry": {"type": "Polygon", "coordinates": []}},
            {"geometry": {"type": "Polygon", "coordinates": [[]]}},
            {"geometry": {"type": "Polygon", "coordinates": [ring]}},
            {
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[], [ring]],
                }
            },
        ],
    )

    assert len(axes.patches) == 2
