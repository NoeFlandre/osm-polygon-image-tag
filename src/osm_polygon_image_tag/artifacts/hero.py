"""Expose the packaged dataset-card hero image."""

from importlib.resources import files
from pathlib import Path

HERO_PNG_RELATIVE = "assets/hero.png"


def packaged_hero_path() -> Path:
    """Return the installed hero image resource path."""
    return Path(str(files("osm_polygon_image_tag").joinpath("_data/hero.png")))
