# OSM Polygon Image Tag

An independent, reproducible pipeline for a GeoParquet dataset of OpenStreetMap
area features carrying raw image-reference tags. The pipeline reads Geofabrik
`.osm.pbf` files recursively, deterministically, and without ever writing to
the source tree. It produces one GeoParquet shard per source PBF, a matching
manifest, deterministic global statistics, and a generated Hugging Face
dataset card, then publishes the verified artifacts to a Hugging Face dataset.

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
  responsibility into `core`, `ingest`, `artifacts`, `runtime`, and
  `integrations` subpackages. See
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
- `run`: process or resume every PBF locally. Each completed PBF produces
  one atomic GeoParquet shard and one atomic manifest. Skipped PBFs reuse
  the verified shard without rehashing the source or output.
- `verify`: revalidate every finalized shard by recomputing the source and
  output SHA-256 and re-reading the Parquet file. This is the explicit
  deep verification path. Use it after a suspected corruption event.
- `rebuild-metadata`: rebuild the catalog, statistics JSON, and dataset
  card from the existing shards. Useful after schema changes that you
  already absorbed, or to refresh the card without touching PBFs.
- `publish`: publish only the existing verified artifacts to the
  configured Hugging Face dataset. It verifies every changed remote file
  before atomically recording a local receipt.
- `run-and-publish`: process one PBF, regenerate metadata, publish, and
  continue with the next PBF.

### Fast resume versus explicit deep verification

`run` and `run-and-publish` use the fast resume path: they trust the
manifest's recorded source size, mtime, and contract versions and skip
shards that still match. They do not rehash the source PBF or re-read the
Parquet file. `verify` is the explicit deep verification path and is the
recommended command to prove the data root is still healthy.

### Skipped PBFs do not regenerate or publish metadata

`run-and-publish` only runs `generate_metadata` and the publisher after a
shard is newly built. A pure resume that only skips previously verified
PBFs will not refresh the catalog, will not regenerate the dataset card,
and will not commit to Hugging Face. Use `rebuild-metadata` or `publish`
explicitly when you want to refresh those.

### Heartbeats and progress events

Every long-running command emits JSON events to stderr, one per line:

```
progress {"event":"run_started","pbf_count":3,"pbf_bytes":12345}
progress {"event":"pbf_started","pbf_index":1,"pbf_count":3,"source_pbf":"europe/france.osm.pbf"}
progress {"event":"pbf_completed","pbf_index":1,"pbf_count":3,"status":"built","accepted_rows":42,"rejections":{}}
progress {"event":"metadata_started"}
progress {"event":"heartbeat","last_event":"metadata_manifest_scan_started","elapsed_seconds":42}
```

Heartbeats keep long-running runs observable without flooding the log. The
final summary is printed to stdout as one canonical JSON object.

### Safe Ctrl-C behaviour

`SIGINT` (Ctrl-C) and `SIGTERM` request a graceful stop. The orchestrator
finishes the current PBF, then stops before starting the next one. Already
finalized shards remain valid; an interrupted build leaves no promoted
artifact because promotion only happens through atomic rename. The next
run picks up exactly where the previous one stopped.

## License

Pipeline source code is Apache-2.0. OpenStreetMap-derived data remains
subject to the Open Database License (ODbL); the generated dataset card
records attribution to the OpenStreetMap contributors and to Geofabrik.

See [`LICENSE`](LICENSE), [`CONTRIBUTING.md`](CONTRIBUTING.md), and
[`docs/`](docs/).
