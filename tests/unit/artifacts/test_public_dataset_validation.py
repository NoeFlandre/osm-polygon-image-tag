from osm_polygon_image_tag.artifacts.public_dataset_validation import (
    public_polygon_schema,
)


def test_public_dataset_validation_owns_release_schema() -> None:
    assert public_polygon_schema.__module__ == (
        "osm_polygon_image_tag.artifacts.public_dataset_validation"
    )
