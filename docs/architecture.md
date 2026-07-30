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
  cli.py             # argparse entry point, exit codes, signal wiring
  __init__.py        # __version__
  py.typed           # typed-package marker
  _data/             # package data (osmium-export.json)
  core/              # configuration, errors, schema, manifest, progress
  ingest/            # PBF discovery, osmium subprocess, tag store, transform
  artifacts/         # storage, catalog, reporting, publication planner
  runtime/           # pipeline, orchestrator, preflight, resources
  integrations/      # provider adapters (Hugging Face Hub)
```

The dependency arrow flows downward:

```
cli  ->  runtime, artifacts, integrations
runtime  ->  core, ingest, artifacts, integrations
artifacts  ->  core, integrations
ingest  ->  core
integrations  ->  core
core  ->  (stdlib only)
```

No layer may import upward. Integrations never imports from runtime, ingest,
artifacts, or core beyond the shared error types it needs to translate
provider SDK failures.

## Why the layering matters

- The PBF source tree is treated as immutable input; only `ingest` reads it.
- The managed data root is the only place any artifact is written; only
  `artifacts` and `runtime` write to it.
- Hugging Face SDK code lives in a single module; the rest of the project
  depends only on the structural `Hub` protocol.
- Core contracts have zero runtime dependencies, so they can be reused in
  tests, scripts, and downstream analysis without dragging in the full
  pipeline.

## Public surfaces

- `osm_polygon_image_tag.cli:run` is the installed entry point.
- `osm_polygon_image_tag.__version__` reports the package version.
- The CLI commands `preflight`, `run`, `run-and-publish`, `publish`,
  `verify`, and `rebuild-metadata` are documented in
  [`operations.md`](operations.md).
- The schema, processing contract, and tag-selection contract are
  documented in [`data-contract.md`](data-contract.md).
