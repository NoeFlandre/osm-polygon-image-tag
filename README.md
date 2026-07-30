# OSM Polygon Image Tag

An independent, reproducible pipeline for a GeoParquet dataset of OpenStreetMap
area features carrying raw image-reference tags. The pipeline reads Geofabrik
`.osm.pbf` files recursively, deterministically, and without ever writing to
the source tree. It produces one GeoParquet shard per source PBF, a matching
manifest, and an independently resumable one-to-many image-asset shard. Asset
resolution reuses polygon Parquet and a transactional cache, so historical
PBFs are never reprocessed merely to add directly usable URLs. Verified
polygon and asset artifacts publish as two Hugging Face configurations.

- Source: <https://github.com/NoeFlandre/osm-polygon-image-tag>
- Dataset: <https://huggingface.co/datasets/NoeFlandre/osm-polygon-image-tag>

## Selected tags

A polygon or multipolygon observation is selected when the way or relation
itself contains at least one non-empty value for any of:

- `image`
- `wikimedia_commons`
- `mapillary`
- `panoramax`
- indexed `panoramax:<n>` where `<n>` is one or more ASCII digits
- `kartaview`
- `flickr`
- `bubbleid` (Bing Streetside)

Keys such as `panoramax:left`, `panoramax:`, `panoramax:1:foo`, `image:license`,
or `contact:flickr` do not match. Every original OSM tag is preserved
losslessly in the `tags` map column; indexed Panoramax entries are also
preserved in the `panoramax_values` map with their original keys, and
`bubbleid` lives in its own nullable column.

## Layout

- `src/osm_polygon_image_tag/`: production package, organized by
  responsibility into `core`, `ingest`, `assets`, `resolvers`, `artifacts`,
  `runtime`, and `integrations` subpackages. See
  [`docs/architecture.md`](docs/architecture.md).
- `tests/`: `unit/` tests mirror the production layout. `integration/`
  tests exercise the real `osmium` binary. `fixtures/` holds stable OSM XML
  inputs.
- `docs/`: current-facing documentation, including `architecture.md`,
  `data-contract.md`, `operations.md`, and `development.md`.

## Commands

Every command accepts `--source-root` and `--data-root`. The production
pipeline runs the following commands against a real read-only PBF tree and a
real managed data root, but the same commands work against any path on the
local filesystem.

```bash
uv run osm-polygon-image-tag preflight \
  --source-root "<source pbf tree>" \
  --data-root "<managed data root>"

uv run osm-polygon-image-tag run \
  --source-root "<source pbf tree>" \
  --data-root "<managed data root>"

uv run osm-polygon-image-tag verify \
  --source-root "<source pbf tree>" \
  --data-root "<managed data root>"

uv run osm-polygon-image-tag rebuild-metadata \
  --source-root "<source pbf tree>" \
  --data-root "<managed data root>"

uv run osm-polygon-image-tag publish \
  --source-root "<source pbf tree>" \
  --data-root "<managed data root>" \
  --confirm-repo NoeFlandre/osm-polygon-image-tag

uv run osm-polygon-image-tag run-and-publish \
  --source-root "<source pbf tree>" \
  --data-root "<managed data root>" \
  --confirm-repo NoeFlandre/osm-polygon-image-tag
```

Authenticate first with `hf auth login`.

### What each command does

- `preflight`: read-only environment check. Reports `osmium` version,
  capacity, and the discovered PBF inventory. Mutates nothing.
- `run`: process or resume every PBF locally while backfilling missing asset
  shards from finalized polygon Parquet. Compatible polygon and asset shards
  are fast-skipped.
- `verify`: deeply revalidate polygon source/output identities and every
  finalized asset shard SHA-256, row count, and Parquet schema.
- `rebuild-metadata`: rebuild the catalog, statistics JSON, and dataset
  card from the existing shards. Useful after schema changes that you
  already absorbed, or to refresh the card without touching PBFs.
- `publish`: publish only the existing verified artifacts to the
  configured Hugging Face dataset. It verifies every changed remote file
  before atomically recording a local receipt.
- `run-and-publish`: perform extraction/backfill, regenerate factual metadata
  when outputs changed, and publish verified polygon and asset shards.

### Fast resume versus explicit deep verification

`run` and `run-and-publish` use the fast resume path: they trust the
manifest's recorded source size, mtime, and contract versions and skip
shards that still match. They do not rehash the source PBF or re-read the
Parquet file. `verify` is the explicit deep verification path and is the
recommended command to prove the data root is still healthy.

### Smart historical backfill

Every finalized polygon shard is queued for enrichment. A compatible asset
manifest skips in constant time; a missing asset shard reads only needed
Parquet columns and consults `cache/resolutions.sqlite`. It never opens the
original PBF. If no polygon or asset output changed, publication receipts
prevent a redundant Hub commit.

After extraction reaches a safe boundary, long asset backfills regenerate
metadata and publish a coalesced checkpoint every 25 completed asset shards,
followed by one final receipt-aware publication.

Mapillary direct URLs require `MAPILLARY_ACCESS_TOKEN`; Flickr direct URLs
require `FLICKR_API_KEY`. Without them the dataset records a factual page URL
and `resolved_page_only`. Credentials are environment-only and never written
to Parquet, manifests, logs, or publication artifacts.

On Hugging Face, use `polygons` (default) for geometry and `image_assets` for
resolved references. Join on `osm_type`, `osm_id`, `osm_version`, and
`source_pbf`. Wikimedia Commons category rows are marked
`category_membership`; membership does not prove depiction.

### Heartbeats and progress events

Every long-running command emits JSON events to stderr, one per line:

```
progress {"event":"run_started","pbf_count":3,"pbf_bytes":12345}
progress {"event":"pbf_started","pbf_index":1,"pbf_count":3,"source_pbf":"europe/france.osm.pbf"}
progress {"event":"pbf_completed","pbf_index":1,"pbf_count":3,"status":"built","accepted_rows":42,"rejections":{}}
progress {"event":"asset_shard_started","asset_index":1,"asset_count":3,"polygon_shard":"data/example.parquet"}
progress {"event":"asset_reference_progress","reference_index":1,"reference_count":8}
progress {"event":"metadata_started"}
progress {"event":"heartbeat","last_event":"metadata_manifest_scan_started","elapsed_seconds":42}
```

Asset events cover backfill/shard lifecycle, reference progress, provider
cooldowns, and final counts. Heartbeats keep long runs observable. The final
summary is canonical JSON on stdout. Non-TTY output and `--log-format json`
are stable JSON; TTY `auto` mode uses restrained Rich/tqdm rendering.

### Safe Ctrl-C behaviour

`SIGINT` (Ctrl-C) and `SIGTERM` set a stop token so the orchestrator starts no
next PBF after the current build returns. If the terminal signal also aborts
the active `osmium` process, that shard fails without promoting a partial
artifact. Already finalized shards remain valid, and the next run resumes from
the last finalized boundary.

## License

Pipeline source code is Apache-2.0. OpenStreetMap-derived data remains
subject to the Open Database License (ODbL); the generated dataset card
records attribution to the OpenStreetMap contributors and to Geofabrik.

See [`LICENSE`](LICENSE), [`CONTRIBUTING.md`](CONTRIBUTING.md), and
[`docs/`](docs/).
