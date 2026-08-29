import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice
from typing import Any

from pyproj import Geod
from shapely import bounds, from_wkb, get_type_id, is_empty, is_valid, orient_polygons, to_wkb
from shapely.errors import GEOSException

from osm_polygon_image_tag.core.contracts import PANORAMAX_VALUES_COLUMN
from osm_polygon_image_tag.ingest.extraction import (
    TARGET_TAG_KEYS,
    ExportRecord,
    has_target_tag,
    panoramax_tag_values,
)

_GEOD = Geod(ellps="WGS84")
_DEFAULT_TRANSFORM_BATCH_SIZE = 512


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


def transform_records(
    records: Iterable[ExportRecord],
    *,
    source_pbf: str,
    batch_size: int = _DEFAULT_TRANSFORM_BATCH_SIZE,
) -> Iterator[AcceptedRow | RejectedRow]:
    """Transform records in bounded batches while preserving scalar semantics."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    iterator = iter(records)
    while batch := list(islice(iterator, batch_size)):
        outcomes = _try_transform_batch(batch, source_pbf)
        if outcomes is None:
            yield from _transform_scalar(batch, source_pbf)
            continue
        yield from outcomes


def _try_transform_batch(records: list[ExportRecord], source_pbf: str) -> list[AcceptedRow] | None:
    if not _batch_has_target_tags(records):
        return None
    geometries = _decode_batch(records)
    if geometries is None:
        return None
    if not _batch_is_eligible(geometries):
        return None
    return _safe_transform_valid_batch(records, geometries, source_pbf)


def _batch_has_target_tags(records: Iterable[ExportRecord]) -> bool:
    return all(has_target_tag(record.tags) for record in records)


def _safe_transform_valid_batch(
    records: list[ExportRecord], geometries: Any, source_pbf: str
) -> list[AcceptedRow] | None:
    try:
        return _transform_valid_batch(records, geometries, source_pbf)
    except (ValueError, GEOSException):
        return None


def _decode_batch(records: list[ExportRecord]) -> Any | None:
    try:
        return from_wkb([bytes.fromhex(record.geometry_ewkb_hex) for record in records])
    except (ValueError, GEOSException):
        return None


def _batch_is_eligible(geometries: Any) -> bool:
    type_ids = get_type_id(geometries)
    return bool((is_valid(geometries) & ~is_empty(geometries) & _polygon_type_mask(type_ids)).all())


def _polygon_type_mask(type_ids: Any) -> Any:
    return (type_ids == 3) | (type_ids == 6)


def _transform_valid_batch(
    records: list[ExportRecord], geometries: Any, source_pbf: str
) -> list[AcceptedRow]:
    normalized = [
        _normalized_geometry(geometry, record.osm_type)
        for record, geometry in zip(records, geometries, strict=True)
    ]
    oriented = orient_polygons(normalized, exterior_cw=False)
    batch_bounds = bounds(oriented)
    outcomes: list[AcceptedRow] = []
    for record, geometry, bounds_row in zip(records, oriented, batch_bounds, strict=True):
        outcomes.append(_transform_valid_row(record, geometry, bounds_row, source_pbf))
    return outcomes


def _transform_valid_row(
    record: ExportRecord, geometry: Any, bounds_row: Any, source_pbf: str
) -> AcceptedRow:
    finite_bounds = _finite_bounds_values(bounds_row)
    if finite_bounds is None:
        raise ValueError("batch geometry bounds are not finite")
    area_m2 = _positive_area(geometry)
    if area_m2 is None:
        raise ValueError("batch geometry area is not positive")
    timestamp = _timestamp(record.timestamp)
    return AcceptedRow(
        values=_accepted_values(
            record,
            source_pbf,
            (geometry, finite_bounds, area_m2),
            timestamp,
        )
    )


def _transform_scalar(
    records: Iterable[ExportRecord], source_pbf: str
) -> Iterator[AcceptedRow | RejectedRow]:
    for record in records:
        yield transform_record(record, source_pbf=source_pbf)


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
    return orient_polygons(_normalized_geometry(geometry, osm_type), exterior_cw=False)


def _normalized_geometry(geometry: Any, osm_type: str) -> Any:
    if osm_type == "way" and geometry.geom_type == "MultiPolygon" and len(geometry.geoms) == 1:
        return geometry.geoms[0]
    return geometry


def _finite_bounds(geometry: Any) -> tuple[float, ...] | None:
    return _finite_bounds_values(geometry.bounds)


def _finite_bounds_values(values: Iterable[float]) -> tuple[float, ...] | None:
    bounds = tuple(float(value) for value in values)
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
