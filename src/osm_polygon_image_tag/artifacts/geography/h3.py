"""H3 cell assignment, coordinate validation, and antimeridian geometry."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

import h3

from .models import GeographicMapError

# H3 resolution used for the dataset-card polygon density map. This is a
# dataset-card artifact contract, not a user-facing analysis sweep.
DEFAULT_H3_RESOLUTION: int = 3


def assign_h3_cell(lat: float, lon: float, *, resolution: int = DEFAULT_H3_RESOLUTION) -> str:
    """Map a centroid to its H3 cell id at the requested resolution.

    Raises :class:`GeographicMapError` on null, NaN, out-of-range, or
    non-finite coordinates or on an invalid resolution.
    """
    if lat is None or lon is None:
        raise GeographicMapError("Latitude and longitude must not be null.")
    try:
        lat_value = float(lat)
        lon_value = float(lon)
    except (TypeError, ValueError) as error:
        raise GeographicMapError(
            f"Latitude and longitude must be numeric; got lat={lat!r}, lon={lon!r}."
        ) from error
    if not (math.isfinite(lat_value) and math.isfinite(lon_value)):
        raise GeographicMapError(
            f"Latitude and longitude must be finite; got lat={lat_value!r}, lon={lon_value!r}."
        )
    if not (-90.0 <= lat_value <= 90.0):
        raise GeographicMapError(f"Latitude {lat_value} is outside the [-90, 90] range.")
    if not (-180.0 <= lon_value <= 180.0):
        raise GeographicMapError(f"Longitude {lon_value} is outside the [-180, 180] range.")
    if (
        not isinstance(resolution, int)
        or isinstance(resolution, bool)
        or not (0 <= resolution <= 15)
    ):
        raise GeographicMapError(f"H3 resolution must be an int in [0, 15]; got {resolution!r}.")
    try:
        return str(h3.latlng_to_cell(lat_value, lon_value, resolution))
    except (ValueError, h3.H3ValueError) as error:
        raise GeographicMapError(
            f"Could not assign H3 cell for ({lat_value}, {lon_value}) "
            f"at resolution {resolution}: {error}"
        ) from error


def _clip_longitude(
    points: Sequence[tuple[float, float]],
    boundary: float,
    *,
    keep_greater: bool,
) -> list[tuple[float, float]]:
    """Clip ``points`` against one vertical longitude boundary."""
    if not points:
        return []

    def inside(point: tuple[float, float]) -> bool:
        return point[0] >= boundary if keep_greater else point[0] <= boundary

    def intersection(start: tuple[float, float], end: tuple[float, float]) -> tuple[float, float]:
        delta = end[0] - start[0]
        if delta == 0.0:
            return (boundary, start[1])
        ratio = (boundary - start[0]) / delta
        return (boundary, start[1] + ratio * (end[1] - start[1]))

    output: list[tuple[float, float]] = []
    if not points:
        return output
    previous = points[-1]
    previous_inside = inside(previous)
    for current in points:
        current_inside = inside(current)
        if current_inside:
            if not previous_inside:
                output.append(intersection(previous, current))
            output.append(current)
        elif previous_inside:
            output.append(intersection(previous, current))
        previous = current
        previous_inside = current_inside
    return output


def split_antimeridian(points: Sequence[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Clip an antimeridian-crossing polygon into closed local rings.

    Merely splitting at a longitude jump leaves open fragments. A plotting
    library then closes those fragments with a world-spanning segment. We
    instead unwrap the polygon, clip it against each 360-degree world slab,
    and shift the resulting closed polygons back into ``[-180, 180]``.
    """
    if len(points) < 3:
        return [list(points)]
    if all(abs(points[index][0] - points[index - 1][0]) <= 180.0 for index in range(len(points))):
        return [list(points)]

    unwrapped = [points[0]]
    for lon, lat in points[1:]:
        previous_lon = unwrapped[-1][0]
        while lon - previous_lon > 180.0:
            lon -= 360.0
        while lon - previous_lon < -180.0:
            lon += 360.0
        unwrapped.append((lon, lat))

    min_slab = math.floor((min(lon for lon, _ in unwrapped) + 180.0) / 360.0)
    max_slab = math.floor((max(lon for lon, _ in unwrapped) + 180.0) / 360.0)
    rings: list[list[tuple[float, float]]] = []
    for slab in range(min_slab, max_slab + 1):
        left = -180.0 + 360.0 * slab
        right = 180.0 + 360.0 * slab
        clipped = _clip_longitude(
            _clip_longitude(unwrapped, left, keep_greater=True),
            right,
            keep_greater=False,
        )
        if len(clipped) >= 3:
            rings.append([(lon - 360.0 * slab, lat) for lon, lat in clipped])
    return rings


def cell_rings(h3_cell: str) -> list[list[tuple[float, float]]]:
    """Return the antimeridian-split ``(lon, lat)`` rings for ``h3_cell``.

    The H3 ``cell_to_boundary`` API returns an iterable of ``(lat, lon)``
    pairs; we swap the order to ``(lon, lat)`` to match matplotlib's
    Cartesian convention.
    """
    try:
        boundary = h3.cell_to_boundary(h3_cell)
    except (ValueError, h3.H3ValueError) as error:
        raise GeographicMapError(f"Could not fetch boundary for {h3_cell}: {error}") from error
    if not boundary:
        return []
    raw_points: list[tuple[float, float]] = []
    for pair in boundary:
        if len(pair) >= 2:
            lat = float(pair[0])
            lon = float(pair[1])
            raw_points.append((lon, lat))
    rings: list[list[tuple[float, float]]] = []
    for ring in split_antimeridian(raw_points):
        if len(ring) >= 3:
            rings.append(ring)
    return rings


def aggregate_centroids_to_cells(
    centroids: Iterable[tuple[float, float]],
    *,
    resolution: int = DEFAULT_H3_RESOLUTION,
) -> list[str]:
    """Return the sorted H3 cell ids for every centroid.

    Provided as a pure helper for the pipeline; it does not deduplicate
    counts. Duplicates are summed at the call site.
    """
    cells: list[str] = []
    for lat, lon in centroids:
        cells.append(assign_h3_cell(lat, lon, resolution=resolution))
    cells.sort()
    return cells


__all__ = [
    "DEFAULT_H3_RESOLUTION",
    "aggregate_centroids_to_cells",
    "assign_h3_cell",
    "cell_rings",
    "split_antimeridian",
]
