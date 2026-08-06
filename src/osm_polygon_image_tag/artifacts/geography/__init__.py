"""Deterministic geographic distribution map for the Hugging Face dataset card.

This subpackage owns the H3-resolved polygon density map that is rendered
into ``assets/geographic_polygon_density.png`` and embedded in the generated
README. The map is built only from finalized ``polygons`` Parquet shards.
The raw PBF source tree is read-only and never opened here.

Modules:

- :mod:`.models` typed dataclasses and the dedicated :class:`GeographicMapError`.
- :mod:`.cache` private cache validation, deterministic serialization, and atomic persistence.
- :mod:`.h3` coordinate validation, H3 cell assignment, antimeridian-safe rings.
- :mod:`.inputs` column-pruned, batched reads of finalized polygon shards.
- :mod:`.basemap` bundled Natural Earth 110m land GeoJSON loader.
- :mod:`.render` matplotlib renderer (atomic PNG writes, log normalization).
- :mod:`.pipeline` per-shard aggregation, cache reuse decisions, and combined
  public map generation.

The public entry point is :func:`pipeline.build_geographic_map`, used by
:mod:`osm_polygon_image_tag.artifacts.reporting` to enrich the dataset
statistics and the dataset card.
"""

from .models import (
    GeographicMapError,
    GeometryCentroid,
    PolygonCountCell,
    RenderResult,
)

__all__ = [
    "GeographicMapError",
    "GeometryCentroid",
    "PolygonCountCell",
    "RenderResult",
]
