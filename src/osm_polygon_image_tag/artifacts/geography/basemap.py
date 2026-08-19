"""Bundled Natural Earth 110m land GeoJSON loader for the dataset-card map.

The 110m landmass reference is bundled as a package data file under
``artifacts/geography/_data/ne_110m_land.geojson``. The basemap loader
reads that file from the installed package and exposes the parsed
GeoJSON ``features`` list; it does NOT perform any network I/O.

The asset is a public-domain Natural Earth 1:110m reference:

    Natural Earth, https://www.naturalearthdata.com (public domain).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

LOGGER = logging.getLogger(__name__)

# Stable, central references to the bundled basemap asset. Do not
# scatter this string across the codebase.
BUNDLED_BASEMAP_FILENAME: str = "ne_110m_land.geojson"


def _bundled_asset_path() -> Path:
    """Return the path to the bundled Natural Earth 110m land GeoJSON."""
    return Path(
        str(
            files("osm_polygon_image_tag").joinpath(
                "artifacts/geography/_data/ne_110m_land.geojson"
            )
        )
    )


def _read_features(path: Path, *, missing_message: str | None, label: str) -> list[Any] | None:
    if not path.exists() or path.stat().st_size == 0:
        if missing_message is not None:
            LOGGER.warning("%s: %s", missing_message, path)
        return None
    return _feature_list(_read_json(path, label))


def _read_json(path: Path, label: str) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        LOGGER.warning("Could not read %s GeoJSON: %s", label, error)
        return None


def _feature_list(data: object | None) -> list[Any] | None:
    if not isinstance(data, dict):
        return None
    return cast(list[Any], data.get("features") or [])


def load_land_basemap(cache_dir: Path | None = None) -> list[Any] | None:
    """Load the Natural Earth 110m land GeoJSON ``features`` list.

    When ``cache_dir`` is supplied, only ``cache_dir`` is consulted;
    this mirrors the Wikidata map stack's runtime cache contract.
    Otherwise the bundled package asset is loaded from the installed
    wheel. The function returns the parsed ``features`` list, or
    ``None`` if the file is missing or unreadable. It never raises;
    it logs a warning so the renderer can produce an ocean-only
    world map if the asset is missing.
    """
    if cache_dir is not None:
        return _read_features(
            cache_dir / BUNDLED_BASEMAP_FILENAME,
            missing_message=None,
            label="cached land",
        )
    return _read_features(
        _bundled_asset_path(),
        missing_message="Bundled Natural Earth land GeoJSON is missing or empty",
        label="bundled land",
    )


def _feature_geometry(feature: object) -> tuple[str, Any] | None:
    if not isinstance(feature, dict):
        return None
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    if not coordinates:
        return None
    return str(geometry.get("type")), coordinates


def _draw_feature(ax: Any, feature: object, mpatches: Any) -> None:
    geometry = _feature_geometry(feature)
    if geometry is None:
        return
    gtype, coordinates = geometry
    _draw_geometry(ax, gtype, coordinates, mpatches)


def _draw_geometry(ax: Any, gtype: str, coordinates: Any, mpatches: Any) -> None:
    if gtype == "Polygon":
        _draw_land_ring(ax, coordinates[0], mpatches)
    elif gtype == "MultiPolygon":
        for polygon in coordinates:
            if polygon:
                _draw_land_ring(ax, polygon[0], mpatches)


def draw_landmasses(ax: Any, features: Sequence[Any]) -> None:
    """Draw Natural Earth landmasses on ``ax``."""
    import matplotlib.patches as mpatches

    for feature in features:
        _draw_feature(ax, feature, mpatches)


def _draw_land_ring(ax: Any, ring: Sequence[Sequence[float]], mpatches: Any) -> None:
    if not ring or len(ring) < 3:
        return
    patch = mpatches.Polygon(
        [(float(lon), float(lat)) for lon, lat in ring],
        closed=True,
        facecolor="#e8e0d0",
        edgecolor="#b8aa90",
        linewidth=0.2,
        zorder=1,
    )
    ax.add_patch(patch)


__all__ = ["BUNDLED_BASEMAP_FILENAME", "draw_landmasses", "load_land_basemap"]
