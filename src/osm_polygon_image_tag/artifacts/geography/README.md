# `artifacts/geography/`

Deterministic H3 polygon-density map for the Hugging Face dataset card.

## What this package owns

- The dataset-card geographic PNG (`assets/geographic_polygon_density.png`).
- The private per-shard cache under `cache/geographic-density/` that
  short-circuits regeneration during repeated metadata runs.
- The bundled Natural Earth 1:110m landmass reference GeoJSON at
  `artifacts/geography/_data/ne_110m_land.geojson` (public domain).

## Modules

- `models`: typed dataclasses for cells, statistics, results, and the
  dedicated `GeographicMapError`.
- `cache`: private cache paths, schema validation, deterministic JSON
  serialization, input digests, and atomic persistence.
- `h3`: coordinate validation, H3 cell assignment at resolution 3,
  antimeridian-safe cell rings.
- `inputs`: column-pruned, batched reads of finalized `polygons`
  GeoParquet shards via `pyarrow.ParquetFile.iter_batches`. Only
  `geometry` and `geometry_type` are read.
- `basemap`: loader and renderer for the bundled Natural Earth GeoJSON.
- `render`: deterministic matplotlib PNG renderer (1600×800 at 100 DPI,
  world extent, `magma` `LogNorm`, atomic file writes).
- `pipeline`: per-shard aggregation, cache reuse decisions, combined
  statistics, and the public `build_geographic_map` entry point.

## Contracts

- H3 resolution is fixed at `DEFAULT_H3_RESOLUTION = 3`.
- Each polygon row contributes exactly once via its Shapely geometry
  centroid in OGC:CRS84; bounding-box midpoints are not used.
- Metadata generation passes the deduplicated public view, so overlapping
  extracts do not inflate the published map. Calling `build_geographic_map`
  without an explicit manifest list still reports the supplied finalized
  per-PBF observations.
- The data root is never asked to read a PBF file.
- The basemap asset is bundled with the package; no network call is
  performed during metadata generation.
- The cache is private and never included in the publication inventory.

## Focused tests

```bash
uv run pytest tests/unit/artifacts/geography -q --no-cov
uv run pytest tests/unit/artifacts/test_geography_reporting.py -q --no-cov
```
