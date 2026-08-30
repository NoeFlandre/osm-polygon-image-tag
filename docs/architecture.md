# Architecture

`osm-polygon-image-tag` is a single-purpose pipeline that reads immutable
Geofabrik PBF files, extracts OpenStreetMap polygon and multipolygon
observations carrying image-reference tags, writes one deterministic
GeoParquet shard per source PBF, and publishes a verified, deduplicated public
view to a Hugging Face dataset. The per-PBF shards remain private inputs for
resume and audit. The codebase is a `uv`-managed Python 3.12 project
organized into responsibility-based subpackages.

The public view has three tables: `polygons` keeps one current row per OSM
object, `images` keeps one row per unique provider image, and `polygon_images`
keeps the many-to-many links between them. This separates image identity from
the relationships that explain where each image reference came from.

## Layered structure

```
src/osm_polygon_image_tag/
  cli.py             # Typer entry point, dependency wiring, exit codes
  __init__.py        # __version__
  py.typed           # typed-package marker
  _data/             # package data (osmium-export.json)
  core/              # configuration, errors, schema, manifest, atomic writes, progress
  ingest/            # PBF discovery, osmium subprocess, tag store, transform
  assets/            # asset schema, cache, manifests, deterministic shards
  resolvers/         # hardened HTTP boundary and provider adapters
  artifacts/         # storage, inventory, catalog, reporting, publication
  artifacts/geography/  # H3 + matplotlib map: models, cache, h3, inputs, basemap, render, pipeline
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

Within the public-asset materialization boundary, `public_asset_schema` owns
the public image/link Arrow contracts and validators, `public_asset_checkpoint`
owns checkpoint selection, safety, limits, and compatibility, and
`public_assets` owns deduplication and output assembly. `public_dataset` uses
the schema contracts directly when validating the final release.

## Why the layering matters

- The PBF source tree is treated as immutable input; only `ingest` reads it.
- The managed data root is the only place any artifact is written; only
  `artifacts` and `runtime` write to it.
- Focused public-asset modules keep persisted contracts and checkpoint policy
  independently testable without coupling them to the SQLite deduplication
  loop.
- Asset schema/resolver contracts are versioned independently from polygon
  extraction. Historical enrichment consumes finalized Parquet and never
  invalidates schema-v2 polygon shards.
- Credential-aware asset refresh and retry-cooldown decisions are implemented
  once as a pure policy boundary and reused by both per-reference resolution
  and whole-shard resume checks, preventing those resumability paths from
  drifting apart.
- A bounded enrichment worker overlaps asset backfill with sequential PBF
  extraction. Provider calls use a transactional cache, global concurrency
  bound, provider semaphores, and request pacing.
- Asset shards keep the exact full-tag build pass, while their progress count
  uses only normalized reference columns. Cacheable resolutions are fetched in
  bounded SQLite batches and reused for the rest of the shard, avoiding
  repeated synchronous cache queries without changing resolver or snapshot
  semantics.
- The HTTP boundary validates every DNS answer, pins the validated address,
  revalidates redirects, strips cross-origin credentials, and bounds metadata.
- Hugging Face SDK code lives in a single adapter; artifact planning depends
  only on the structural `Hub` protocol.
- Core owns shared contracts, the Arrow/CRS schema, and the common durable
  atomic-byte-write and managed-output path-safety primitives. It does not
  depend on project orchestration, ingestion, artifacts, integrations, or
  provider SDKs.

## Worker/main-thread coordination

The orchestrator runs sequential PBF extraction on the main thread while a
background enrichment worker processes asset shards concurrently. When a
publisher is configured, both threads may trigger metadata regeneration and
publication: the main thread after each completed PBF, and the worker thread
after every completed asset shard (`every=1`). To prevent the worker's periodic
checkpoints from observing transient PBF-build temporary files (atomic-write
`.tmp` siblings in `data/` and `tag-store-*.sqlite` in `tmp/`), the
orchestrator uses a per-run `threading.Lock` (`refresh_lock`). The main thread
holds this lock for the entire duration of each `build()` call; all refresh
paths (periodic worker checkpoints, explicit main-thread checkpoints, and the
final flush) acquire the same lock before invoking `_refresh_artifacts`. This
ensures that publication inventory validation never sees pipeline-owned
temporary files, while HTTP enrichment work proceeds without serialization.

## Public surfaces

- `osm_polygon_image_tag.cli:run` is the installed entry point.
- `osm_polygon_image_tag.__version__` reports the package version.
- The CLI commands `preflight`, `run`, `run-and-publish`, `publish`,
  `verify`, and `rebuild-metadata` are documented in
  [`operations.md`](operations.md).
- The schema, processing contract, and tag-selection contract are
  documented in [`data-contract.md`](data-contract.md).
