"""Typed dataclasses for the geographic density map.

The :class:`GeographicMapError` is the only exception raised by this
subpackage. The map is a single deterministic static PNG, so the
minimum surface is:

- :class:`GeometryCentroid` -- one decoded polygon centroid discovered in
  a finalized polygon shard.
- :class:`PolygonCountCell` -- one H3 cell's aggregated polygon count.
- :class:`RenderResult` -- one renderer's PNG output path and caption.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class GeographicMapError(RuntimeError):
    """Raised for invalid inputs or dataset-card map generation failures."""


@dataclass(frozen=True, slots=True)
class GeometryCentroid:
    """One decoded polygon centroid discovered in a finalized polygon shard.

    ``shard_relative_path`` is the layout-stable path of the source
    GeoParquet file (under the data root). ``row_index`` is the
    row index inside that shard (zero-based). The centroid is derived
    only from the geometry; bounding boxes and area midpoints are not
    used.
    """

    shard_relative_path: str
    row_index: int
    geometry_type: str
    lon: float
    lat: float

    def to_dict(self) -> dict[str, object]:
        return {
            "shard_relative_path": self.shard_relative_path,
            "row_index": self.row_index,
            "geometry_type": self.geometry_type,
            "lon": self.lon,
            "lat": self.lat,
        }


@dataclass(frozen=True, slots=True)
class PolygonCountCell:
    """One H3 cell's aggregated polygon count.

    Cells are kept sorted by ``h3_cell`` so the aggregate is stable
    across runs. ``polygon_count`` is the raw number of polygon rows
    whose geometry centroid falls into this cell.
    """

    h3_cell: str
    polygon_count: int

    def to_dict(self) -> dict[str, object]:
        return {"h3_cell": self.h3_cell, "polygon_count": self.polygon_count}


@dataclass(frozen=True, slots=True)
class RenderResult:
    """Outcome of a render function.

    The PNG is written to ``output_path`` and the exact caption text
    rendered onto the figure is exposed here so callers and tests can
    introspect it without parsing the rasterized image.
    """

    output_path: Path
    caption: str


@dataclass(frozen=True, slots=True)
class MapStatistics:
    """Deterministic summary of the generated geographic map."""

    h3_resolution: int
    cell_count: int
    polygon_rows: int
    min_cell_count: int
    max_cell_count: int
    input_shard_count: int
    input_digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "h3_resolution": self.h3_resolution,
            "cell_count": self.cell_count,
            "polygon_rows": self.polygon_rows,
            "min_cell_count": self.min_cell_count,
            "max_cell_count": self.max_cell_count,
            "input_shard_count": self.input_shard_count,
            "input_digest": self.input_digest,
        }


@dataclass(frozen=True, slots=True)
class MapResult:
    """Combined output of the geographic map pipeline."""

    cells: tuple[PolygonCountCell, ...]
    statistics: MapStatistics
    render: RenderResult


__all__ = [
    "GeographicMapError",
    "GeometryCentroid",
    "MapResult",
    "MapStatistics",
    "PolygonCountCell",
    "RenderResult",
]
