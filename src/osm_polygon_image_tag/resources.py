from importlib.resources import files
from pathlib import Path


def osmium_export_config() -> Path:
    resource = files("osm_polygon_image_tag").joinpath("_data/osmium-export.json")
    return Path(str(resource))
