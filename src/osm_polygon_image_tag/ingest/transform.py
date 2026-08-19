import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pyproj import Geod
from shapely import from_wkb, orient_polygons, to_wkb
from shapely.errors import GEOSException

from osm_polygon_image_tag.core.contracts import PANORAMAX_VALUES_COLUMN
from osm_polygon_image_tag.ingest.extraction import (
    TARGET_TAG_KEYS,
    ExportRecord,
    has_target_tag,
    panoramax_tag_values,
)

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
    geometry, reason = _validated_geometry(record)
    if reason is not None:
        return RejectedRow(reason=reason)
    try:
        timestamp = _timestamp(record.timestamp)
    except ValueError:
        return RejectedRow(reason="invalid_timestamp")
    return AcceptedRow(values=_accepted_values(record, source_pbf, geometry, timestamp))


def _validated_geometry(
    record: ExportRecord,
) -> tuple[tuple[Any, tuple[float, ...], float] | None, str | None]:
    geometry, reason = _decode_geometry(record.geometry_ewkb_hex)
    if reason is not None:
        return None, reason
    reason = _geometry_rejection(geometry)
    if reason is not None:
        return None, reason
    oriented = _oriented_geometry(geometry, record.osm_type)
    bounds = _finite_bounds(oriented)
    if bounds is None:
        return None, "non_finite_geometry"
    area_m2 = _positive_area(oriented)
    if area_m2 is None:
        return None, "non_positive_area"
    return (oriented, bounds, area_m2), None


def _decode_geometry(value: str) -> tuple[Any | None, str | None]:
    try:
        return from_wkb(bytes.fromhex(value)), None
    except (ValueError, GEOSException):
        return None, "malformed_wkb"


def _geometry_rejection(geometry: Any) -> str | None:
    if geometry.is_empty:
        return "empty_geometry"
    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        return "non_polygon_geometry"
    if not geometry.is_valid:
        return "invalid_geometry"
    return None


def _oriented_geometry(geometry: Any, osm_type: str) -> Any:
    if osm_type == "way" and geometry.geom_type == "MultiPolygon" and len(geometry.geoms) == 1:
        geometry = geometry.geoms[0]
    return orient_polygons(geometry, exterior_cw=False)


def _finite_bounds(geometry: Any) -> tuple[float, ...] | None:
    bounds = tuple(float(value) for value in geometry.bounds)
    return bounds if len(bounds) == 4 and all(math.isfinite(value) for value in bounds) else None


def _positive_area(geometry: Any) -> float | None:
    area, _perimeter = _GEOD.geometry_area_perimeter(geometry)
    area_m2 = abs(float(area))
    return area_m2 if math.isfinite(area_m2) and area_m2 > 0 else None


def _accepted_values(
    record: ExportRecord,
    source_pbf: str,
    geometry_data: tuple[Any, tuple[float, ...], float] | None,
    timestamp: datetime | None,
) -> dict[str, Any]:
    if geometry_data is None:
        raise ValueError("validated geometry is required")
    oriented, bounds, area_m2 = geometry_data
    sorted_tags = dict(sorted(record.tags.items()))
    values = _base_accepted_values(
        record, source_pbf, oriented, bounds, area_m2, timestamp, sorted_tags
    )
    values.update({key: sorted_tags.get(key) for key in TARGET_TAG_KEYS})
    values[PANORAMAX_VALUES_COLUMN] = panoramax_tag_values(sorted_tags)
    return values


def _base_accepted_values(
    record: ExportRecord,
    source_pbf: str,
    oriented: Any,
    bounds: tuple[float, ...],
    area_m2: float,
    timestamp: datetime | None,
    sorted_tags: dict[str, str],
) -> dict[str, Any]:
    return {
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
