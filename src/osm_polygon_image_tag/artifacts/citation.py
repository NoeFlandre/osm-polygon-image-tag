"""Expose the packaged citation metadata."""

from importlib.resources import files
from pathlib import Path

CITATION_CFF_RELATIVE = "citation.cff"


def packaged_citation_path() -> Path:
    """Return the installed citation metadata resource path."""
    return Path(str(files("osm_polygon_image_tag").joinpath("_data/citation.cff")))
