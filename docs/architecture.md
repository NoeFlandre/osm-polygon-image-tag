# Architecture

`osm-polygon-image-tag` is a single-purpose pipeline that reads immutable
Geofabrik PBF files, extracts OpenStreetMap polygon and multipolygon
observations carrying image-reference tags, writes one deterministic
GeoParquet shard per source PBF, and publishes the verified shards to a
Hugging Face dataset. The codebase is a `uv`-managed Python 3.12 project
organized into responsibility-based subpackages.

## Layered structure

```
src/osm_polygon_image_tag/
  cli.py             # Typer entry point, dependency wiring, exit codes
  __init__.py        # __version__
  py.typed           # typed-package marker
  _data/             # package data (osmium-export.json)
  core/              # configuration, errors, schema, manifest, progress
  ingest/            # PBF discovery, osmium subprocess, tag store, transform
  assets/            # asset schema, cache, manifests, deterministic shards
  resolvers/         # hardened HTTP boundary and provider adapters
  artifacts/         # storage, inventory, catalog, reporting, publication
  artifacts/geography/  # H3 + matplotlib map: models, h3, inputs, basemap, render, pipeline
  runtime/           # pipeline, enrichment, console, orchestration
  integrations/      # provider adapters (Hugging Face Hub)
```

The dependency arrow flows downward:

```
cli  ->  runtime, assets, resolvers, artifacts, integrations
runtime  ->  core, ingest, assets, artifacts
resolvers  ->  assets
assets  ->  core
artifacts  ->  core, assets
ingest  ->  core
integrations  ->  core, artifacts
core  ->  stdlib, PyArrow, PyProj
```

No layer may import upward. Provider-neutral publication types live in
`artifacts`; the Hugging Face adapter implements that boundary and translates
provider SDK failures into project errors.

## Why the layering matters

- The PBF source tree is treated as immutable input; only `ingest` reads it.
- The managed data root is the only place any artifact is written; only
  `artifacts` and `runtime` write to it.
- Asset schema/resolver contracts are versioned independently from polygon
  extraction. Historical enrichment consumes finalized Parquet and never
  invalidates schema-v2 polygon shards.
- A bounded enrichment worker overlaps asset backfill with sequential PBF
  extraction. Provider calls use a transactional cache, global concurrency
  bound, provider semaphores, and request pacing.
- The HTTP boundary validates every DNS answer, pins the validated address,
  revalidates redirects, strips cross-origin credentials, and bounds metadata.
- Hugging Face SDK code lives in a single adapter; artifact planning depends
  only on the structural `Hub` protocol.
- Core owns shared contracts and the Arrow/CRS schema. It does not depend on
  project orchestration, ingestion, artifacts, integrations, or provider SDKs.

## Public surfaces

- `osm_polygon_image_tag.cli:run` is the installed entry point.
- `osm_polygon_image_tag.__version__` reports the package version.
- The CLI commands `preflight`, `run`, `run-and-publish`, `publish`,
  `verify`, and `rebuild-metadata` are documented in
  [`operations.md`](operations.md).
- The schema, processing contract, and tag-selection contract are
  documented in [`data-contract.md`](data-contract.md).
