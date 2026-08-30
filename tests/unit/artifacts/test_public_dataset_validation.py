from osm_polygon_image_tag.artifacts.public_dataset_validation import (
    _reuse_inputs_match,
    public_polygon_schema,
)


def test_public_dataset_validation_owns_release_schema() -> None:
    assert public_polygon_schema.__module__ == (
        "osm_polygon_image_tag.artifacts.public_dataset_validation"
    )


def test_public_dataset_validation_owns_reuse_contract() -> None:
    assert _reuse_inputs_match.__module__ == (
        "osm_polygon_image_tag.artifacts.public_dataset_validation"
    )
