from collections.abc import Mapping

import pytest

from osm_polygon_image_tag.assets.references import (
    SourceReference,
    references_from_row,
)


@pytest.mark.parametrize(
    ("key", "value", "kind", "canonical"),
    [
        ("wikimedia_commons", "Category:Brussels Park", "commons_category", "Brussels Park"),
        ("wikimedia_commons", "File:Jam1.jpg", "commons_file", "File:Jam1.jpg"),
        ("image", "Image:Example.jpg", "commons_file", "File:Example.jpg"),
        (
            "panoramax",
            "4492cea4-1018-4285-8074-cf3d37f3c673",
            "panoramax",
            "4492cea4-1018-4285-8074-cf3d37f3c673",
        ),
        ("mapillary", "2627502594079174", "mapillary", "2627502594079174"),
        ("mapillary", "Site 1 In Zharey District", "invalid", "Site 1 In Zharey District"),
        ("kartaview", "9010185/4", "kartaview", "9010185/4"),
        (
            "flickr",
            "https://www.flickr.com/photos/user/6831725321",
            "flickr",
            "6831725321",
        ),
        ("bubbleid", "215977408", "streetside", "215977408"),
        (
            "image",
            "https://example.test/photo.jpg",
            "generic_http",
            "https://example.test/photo.jpg",
        ),
    ],
)
def test_reference_kinds_are_canonicalized(key: str, value: str, kind: str, canonical: str) -> None:
    row: Mapping[str, object] = {"tags": {key: value}, "panoramax_values": {}}

    references = references_from_row(row)

    assert len(references) == 1
    assert references[0].resolver_kind == kind
    assert references[0].canonical_reference == canonical
    assert references[0].source_tag_key == key
    assert references[0].source_tag_value == value


def test_whitespace_normalization_does_not_change_source_value() -> None:
    raw = "  File:Jam1.jpg  "

    reference = references_from_row({"tags": {"wikimedia_commons": raw}, "panoramax_values": {}})[0]

    assert reference.source_tag_value == raw
    assert reference.canonical_reference == "File:Jam1.jpg"


def test_indexed_panoramax_preserves_provenance_for_one_canonical_request() -> None:
    uuid = "4492cea4-1018-4285-8074-cf3d37f3c673"
    references = references_from_row(
        {
            "tags": {"panoramax": uuid, "panoramax:0": uuid},
            "panoramax_values": [
                {"key": "panoramax", "value": uuid},
                {"key": "panoramax:0", "value": uuid},
            ],
        }
    )

    assert references == (
        SourceReference("panoramax", "panoramax", uuid, uuid, "panoramax"),
        SourceReference("panoramax", "panoramax:0", uuid, uuid, "panoramax"),
    )
    assert len({(item.provider, item.canonical_reference) for item in references}) == 1


def test_empty_values_are_not_references() -> None:
    assert references_from_row({"tags": {"image": ""}, "panoramax_values": {}}) == ()
