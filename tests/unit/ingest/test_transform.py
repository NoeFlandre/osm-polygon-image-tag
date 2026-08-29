from datetime import UTC, datetime
from typing import Any

import pytest
from shapely import to_wkb
from shapely.geometry import LineString, MultiPolygon, Polygon

from osm_polygon_image_tag.ingest.extraction import ExportRecord
from osm_polygon_image_tag.ingest.transform import (
    AcceptedRow,
    RejectedRow,
    transform_record,
    transform_records,
)


def _record(geometry: object, **overrides: object) -> ExportRecord:
    values: dict[str, Any] = {
        "geometry_ewkb_hex": to_wkb(geometry, hex=True),
        "osm_type": "way",
        "osm_id": 42,
        "version": 3,
        "changeset": 99,
        "timestamp": "2026-01-01T00:00:00Z",
        "tags": {
            "name": "Place",
            "image": "",
            "wikimedia_commons": "Category:Exact",
        },
    }
    values.update(overrides)
    return ExportRecord(**values)


def test_transforms_polygon_with_exact_tags_provider_values_and_metadata() -> None:
    polygon = Polygon([(2, 48), (2.01, 48), (2.01, 48.01), (2, 48.01)])

    outcome = transform_record(_record(polygon), source_pbf="europe/france.osm.pbf")

    assert isinstance(outcome, AcceptedRow)
    row = outcome.values
    assert row["osm_type"] == "way"
    assert row["osm_id"] == 42
    assert row["osm_version"] == 3
    assert row["osm_changeset"] == 99
    assert row["osm_timestamp"] == datetime(2026, 1, 1, tzinfo=UTC)
    assert row["source_pbf"] == "europe/france.osm.pbf"
    assert row["source_feature_id"] == "europe/france.osm.pbf|way|42|3"
    assert row["geometry_type"] == "Polygon"
    assert row["area_m2"] > 0
    assert (row["bbox_min_lon"], row["bbox_min_lat"]) == pytest.approx((2.0, 48.0))
    assert (row["bbox_max_lon"], row["bbox_max_lat"]) == pytest.approx((2.01, 48.01))
    assert row["tags"] == {
        "image": "",
        "name": "Place",
        "wikimedia_commons": "Category:Exact",
    }
    assert row["image"] == ""
    assert row["wikimedia_commons"] == "Category:Exact"
    assert row["mapillary"] is None


def test_transforms_multipolygon_and_hole_with_positive_geodesic_area() -> None:
    polygon_with_hole = Polygon(
        [(0, 0), (0.02, 0), (0.02, 0.02), (0, 0.02)],
        holes=[[(0.005, 0.005), (0.015, 0.005), (0.015, 0.015), (0.005, 0.015)]],
    )
    second = Polygon([(1, 1), (1.01, 1), (1.01, 1.01), (1, 1.01)])
    relation = _record(
        MultiPolygon([polygon_with_hole, second]),
        osm_type="relation",
        osm_id=7,
        version=None,
        changeset=None,
        timestamp=None,
        tags={"type": "multipolygon", "flickr": "photo"},
    )

    outcome = transform_record(relation, source_pbf="region.osm.pbf")

    assert isinstance(outcome, AcceptedRow)
    assert outcome.values["geometry_type"] == "MultiPolygon"
    assert outcome.values["area_m2"] > 0
    assert outcome.values["osm_timestamp"] is None
    assert outcome.values["source_feature_id"] == "region.osm.pbf|relation|7|null"
    assert outcome.values["tags"] == {"flickr": "photo", "type": "multipolygon"}


def test_preserves_bubbleid_and_sorted_indexed_panoramax_values() -> None:
    polygon = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    record = _record(
        polygon,
        tags={
            "panoramax:2": "second",
            "bubbleid": "bing-360",
            "panoramax": "primary",
            "panoramax:0": "first",
            "panoramax:3": "",
            "panoramax:left": "not-indexed",
        },
    )

    outcome = transform_record(record, source_pbf="region.osm.pbf")

    assert isinstance(outcome, AcceptedRow)
    assert outcome.values["bubbleid"] == "bing-360"
    assert outcome.values["panoramax_values"] == {
        "panoramax": "primary",
        "panoramax:0": "first",
        "panoramax:2": "second",
    }


def test_normalizes_single_part_way_multipolygon_to_polygon() -> None:
    polygon = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])

    outcome = transform_record(_record(MultiPolygon([polygon])), source_pbf="region.osm.pbf")

    assert isinstance(outcome, AcceptedRow)
    assert outcome.values["geometry_type"] == "Polygon"


def test_transform_records_matches_scalar_transform_for_mixed_batches() -> None:
    records = [
        _record(Polygon([(0, 0), (0, 1), (1, 1), (1, 0)]), osm_id=1),
        _record(
            MultiPolygon([Polygon([(2, 2), (2, 3), (3, 3), (3, 2)])]),
            osm_type="relation",
            osm_id=2,
            timestamp=None,
        ),
        _record(Polygon([(0, 0), (1, 1), (1, 0), (0, 1)]), osm_id=3),
        _record(
            Polygon([(0, 0), (0, 1), (1, 1), (1, 0)]),
            osm_id=4,
            tags={"name": "not an image"},
        ),
    ]

    expected = [transform_record(record, source_pbf="region.osm.pbf") for record in records]

    assert list(transform_records(records, source_pbf="region.osm.pbf", batch_size=2)) == expected


def test_transform_records_rejects_nonpositive_batch_size() -> None:
    with pytest.raises(ValueError, match="batch_size must be positive"):
        list(transform_records([], source_pbf="region.osm.pbf", batch_size=0))


@pytest.mark.parametrize(
    ("record", "reason"),
    [
        (_record(LineString([(0, 0), (1, 1)])), "non_polygon_geometry"),
        (_record(Polygon()), "empty_geometry"),
        (_record(Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])), "invalid_geometry"),
        (
            _record(Polygon([(0, 0), (0, 1), (1, 1), (1, 0)]), tags={"name": "none"}),
            "missing_target_tag",
        ),
        (
            _record(Polygon([(0, 0), (0, 1), (1, 1), (1, 0)]), geometry_ewkb_hex="zz"),
            "malformed_wkb",
        ),
    ],
)
def test_rejects_bad_rows_with_stable_reason(record: ExportRecord, reason: str) -> None:
    assert transform_record(record, source_pbf="region.osm.pbf") == RejectedRow(reason=reason)
