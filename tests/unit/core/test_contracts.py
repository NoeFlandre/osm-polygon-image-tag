from osm_polygon_image_tag.artifacts.catalog import PROVIDERS
from osm_polygon_image_tag.assets.polygon_input import (
    POLYGON_COLUMNS,
)
from osm_polygon_image_tag.assets.polygon_input import (
    REFERENCE_COLUMNS as INPUT_REFERENCE_COLUMNS,
)
from osm_polygon_image_tag.core.contracts import (
    IMAGE_REFERENCE_KEYS,
    REFERENCE_COLUMNS,
    SCALAR_REFERENCE_COLUMNS,
)
from osm_polygon_image_tag.ingest.tag_policy import TARGET_TAG_KEYS


def test_image_reference_contract_is_shared_by_all_consumers() -> None:
    assert IMAGE_REFERENCE_KEYS == (
        "image",
        "wikimedia_commons",
        "mapillary",
        "panoramax",
        "kartaview",
        "flickr",
        "bubbleid",
    )
    assert TARGET_TAG_KEYS is IMAGE_REFERENCE_KEYS
    assert PROVIDERS is IMAGE_REFERENCE_KEYS
    assert INPUT_REFERENCE_COLUMNS is REFERENCE_COLUMNS
    assert POLYGON_COLUMNS[-len(REFERENCE_COLUMNS) :] == REFERENCE_COLUMNS
    assert SCALAR_REFERENCE_COLUMNS == (
        "image",
        "wikimedia_commons",
        "mapillary",
        "kartaview",
        "flickr",
        "bubbleid",
    )
