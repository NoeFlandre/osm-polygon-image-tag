import osm_polygon_image_tag.artifacts.public_dataset as public_dataset
from osm_polygon_image_tag.artifacts.public_dataset_output import (
    PublicDatasetResult,
    _manifest_payload,
    _write_public_dataset,
)


def test_public_dataset_output_has_a_focused_owner() -> None:
    assert PublicDatasetResult.__module__ == (
        "osm_polygon_image_tag.artifacts.public_dataset_output"
    )
    assert _manifest_payload.__module__ == ("osm_polygon_image_tag.artifacts.public_dataset_output")
    assert _write_public_dataset.__module__ == (
        "osm_polygon_image_tag.artifacts.public_dataset_output"
    )
    assert public_dataset.PublicDatasetResult is PublicDatasetResult
    assert public_dataset._manifest_payload is _manifest_payload
    assert public_dataset._write_public_dataset is _write_public_dataset
