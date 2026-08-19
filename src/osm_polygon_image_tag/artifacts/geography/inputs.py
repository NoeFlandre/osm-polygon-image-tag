"""Validated, column-pruned reads of finalized polygon Parquet shards.

The map pipeline reads only the ``geometry`` (WKB) and ``geometry_type``
columns from each finalized polygon shard. The geographic centroid is
derived from the Shapely geometry, never persisted, and never added
to the GeoParquet schema. Rows with malformed WKB, missing geometry,
or non-finite coordinates fail closed with a unit-level :class:`GeographicMapError`
that names the offending shard and row index.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq
from shapely import from_wkb
from shapely.errors import GEOSException, ShapelyError
from shapely.geometry import MultiPolygon, Polygon

from osm_polygon_image_tag.artifacts.manifest_inventory import verified_manifests

from .models import GeographicMapError, GeometryCentroid

LOGGER = logging.getLogger(__name__)

# PyArrow metadata columns that are not part of the user schema.
PYARROW_INTERNAL_COLUMNS: frozenset[str] = frozenset(
    {"__fragment_index", "__batch_index", "__last_in_fragment", "__filename"}
)

# The minimum set of columns required to compute the polygon density map.
# ``geometry`` is read as WKB; ``geometry_type`` is bounded by the
# GeoParquet contract to ``Polygon`` or ``MultiPolygon``.
REQUIRED_COLUMNS: tuple[str, ...] = ("geometry", "geometry_type")


def iter_polygon_geometry(
    parquet_path: Path,
    *,
    batch_size: int = 8192,
) -> Iterator[tuple[int, bytes, str]]:
    """Yield ``(row_index, geometry_wkb, geometry_type)`` rows from a shard.

    The reader is column-pruned to :data:`REQUIRED_COLUMNS` and uses
    :func:`pyarrow.parquet.ParquetFile.iter_batches` so memory pressure
    stays bounded by the configured ``batch_size``.
    """
    if batch_size < 1:
        raise GeographicMapError(f"batch_size must be positive, got {batch_size}")
    parquet = _open_polygon_parquet(parquet_path)
    _validate_required_columns(parquet, parquet_path)
    row_base = 0
    for batch in parquet.iter_batches(batch_size=batch_size, columns=list(REQUIRED_COLUMNS)):
        yield from _batch_geometry_rows(parquet_path, batch, row_base)
        row_base += batch.num_rows


def _open_polygon_parquet(path: Path) -> pq.ParquetFile:
    try:
        return pq.ParquetFile(path)
    except (OSError, ValueError) as error:
        raise GeographicMapError(f"Could not read polygon parquet {path}: {error}") from error


def _validate_required_columns(parquet: pq.ParquetFile, path: Path) -> None:
    actual_schema = set(parquet.schema_arrow.names) - PYARROW_INTERNAL_COLUMNS
    missing = [name for name in REQUIRED_COLUMNS if name not in actual_schema]
    if missing:
        raise GeographicMapError(
            f"Polygon parquet {path} is missing required columns: {sorted(missing)}"
        )


def _batch_geometry_rows(
    path: Path, batch: pa.RecordBatch, row_base: int
) -> Iterator[tuple[int, bytes, str]]:
    geometries = batch.column("geometry").to_pylist()
    geometry_types = batch.column("geometry_type").to_pylist()
    for offset, (wkb, geometry_type) in enumerate(zip(geometries, geometry_types, strict=True)):
        row_index = row_base + offset
        yield (
            row_index,
            _coerce_geometry(path, row_index, wkb),
            _coerce_geometry_type(path, row_index, geometry_type),
        )


def _coerce_geometry(path: Path, row_index: int, value: object) -> bytes:
    if value is None:
        raise GeographicMapError(f"Polygon parquet {path} row {row_index}: geometry is null")
    try:
        return bytes(cast(Any, value))
    except (TypeError, ValueError) as error:
        raise GeographicMapError(
            f"Polygon parquet {path} row {row_index}: geometry is not bytes"
        ) from error


def _coerce_geometry_type(path: Path, row_index: int, value: object) -> str:
    if value is None:
        raise GeographicMapError(f"Polygon parquet {path} row {row_index}: geometry_type is null")
    return str(value)


def _centroid_from_wkb(wkb: bytes, geometry_type: str) -> tuple[float, float]:
    """Decode the WKB and compute its Shapely centroid in CRS84."""
    _validate_geometry_type(geometry_type)
    geometry = _decode_geometry(wkb)
    _validate_polygon_geometry(geometry)
    centroid = geometry.centroid
    lon = float(centroid.x)
    lat = float(centroid.y)
    _validate_centroid_coordinates(lon, lat)
    return lon, lat


def _validate_geometry_type(geometry_type: str) -> None:
    if geometry_type not in {"Polygon", "MultiPolygon"}:
        raise GeographicMapError(
            f"Geometry centroid requires Polygon or MultiPolygon, got {geometry_type!r}"
        )


def _decode_geometry(wkb: bytes) -> Polygon | MultiPolygon:
    try:
        geometry = from_wkb(wkb)
    except (GEOSException, ShapelyError, ValueError, TypeError) as error:
        raise GeographicMapError(f"Invalid WKB geometry: {error}") from error
    if not isinstance(geometry, Polygon | MultiPolygon):
        raise GeographicMapError(
            f"Geometry centroid requires Polygon or MultiPolygon, got {type(geometry).__name__}"
        )
    return geometry


def _validate_polygon_geometry(geometry: Polygon | MultiPolygon) -> None:
    if geometry.is_empty:
        raise GeographicMapError("Cannot compute centroid of an empty geometry")


def _validate_centroid_coordinates(lon: float, lat: float) -> None:
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise GeographicMapError(
            f"Geometry centroid yielded non-finite coordinates: x={lon}, y={lat}"
        )


def read_shard_polygon_centroids(
    parquet_path: Path,
    relative_path: str,
    *,
    batch_size: int = 8192,
) -> Iterator[GeometryCentroid]:
    """Yield centroids from one finalized shard without rescanning other shards."""
    for row_index, wkb, geometry_type in iter_polygon_geometry(parquet_path, batch_size=batch_size):
        try:
            lon, lat = _centroid_from_wkb(wkb, geometry_type)
        except GeographicMapError as error:
            raise GeographicMapError(f"{relative_path} row {row_index}: {error}") from error
        yield GeometryCentroid(
            shard_relative_path=relative_path,
            row_index=row_index,
            geometry_type=geometry_type,
            lon=lon,
            lat=lat,
        )


def read_polygon_centroids(
    data_root: Path,
    *,
    batch_size: int = 8192,
) -> Iterator[GeometryCentroid]:
    """Yield every finalized polygon shard's geometry centroid.

    Iteration is deterministic: shards are processed in the order
    returned by :func:`verified_manifests`, which is sorted by
    manifest path. Row indices are local to each shard.
    """
    for manifest, output in verified_manifests(data_root):
        relative_path = manifest.output.relative_path
        yield from read_shard_polygon_centroids(output, relative_path, batch_size=batch_size)


__all__ = [
    "PYARROW_INTERNAL_COLUMNS",
    "REQUIRED_COLUMNS",
    "iter_polygon_geometry",
    "read_polygon_centroids",
    "read_shard_polygon_centroids",
]
