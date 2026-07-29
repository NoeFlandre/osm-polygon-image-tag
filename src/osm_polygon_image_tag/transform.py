import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pyproj import Geod
from shapely import from_wkb, orient_polygons, to_wkb
from shapely.errors import GEOSException

from osm_polygon_image_tag.extraction import TARGET_TAG_KEYS, ExportRecord, has_target_tag

_GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True, slots=True)
class AcceptedRow:
    values: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RejectedRow:
    reason: str


def _timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def transform_record(
    record: ExportRecord,
    *,
    source_pbf: str,
) -> AcceptedRow | RejectedRow:
    if not has_target_tag(record.tags):
        return RejectedRow(reason="missing_target_tag")
    try:
        geometry = from_wkb(bytes.fromhex(record.geometry_ewkb_hex))
    except (ValueError, GEOSException):
        return RejectedRow(reason="malformed_wkb")
    if geometry.is_empty:
        return RejectedRow(reason="empty_geometry")
    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        return RejectedRow(reason="non_polygon_geometry")
    if not geometry.is_valid:
        return RejectedRow(reason="invalid_geometry")
    is_single_way_polygon = (
        record.osm_type == "way"
        and geometry.geom_type == "MultiPolygon"
        and len(geometry.geoms) == 1
    )
    if is_single_way_polygon:
        geometry = geometry.geoms[0]

    oriented = orient_polygons(geometry, exterior_cw=False)
    bounds = tuple(float(value) for value in oriented.bounds)
    if len(bounds) != 4 or not all(math.isfinite(value) for value in bounds):
        return RejectedRow(reason="non_finite_geometry")
    area, _perimeter = _GEOD.geometry_area_perimeter(oriented)
    area_m2 = abs(float(area))
    if not math.isfinite(area_m2) or area_m2 <= 0:
        return RejectedRow(reason="non_positive_area")
    try:
        timestamp = _timestamp(record.timestamp)
    except ValueError:
        return RejectedRow(reason="invalid_timestamp")

    sorted_tags = dict(sorted(record.tags.items()))
    values: dict[str, Any] = {
        "osm_type": record.osm_type,
        "osm_id": record.osm_id,
        "osm_version": record.version,
        "osm_changeset": record.changeset,
        "osm_timestamp": timestamp,
        "source_pbf": source_pbf,
        "source_feature_id": (
            f"{source_pbf}|{record.osm_type}|{record.osm_id}|"
            f"{record.version if record.version is not None else 'null'}"
        ),
        "geometry": to_wkb(oriented, output_dimension=2, byte_order=1),
        "geometry_type": oriented.geom_type,
        "area_m2": area_m2,
        "bbox_min_lon": bounds[0],
        "bbox_min_lat": bounds[1],
        "bbox_max_lon": bounds[2],
        "bbox_max_lat": bounds[3],
        "tags": sorted_tags,
    }
    values.update({key: sorted_tags.get(key) for key in TARGET_TAG_KEYS})
    return AcceptedRow(values=values)
