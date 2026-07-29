# OSM Polygon Image Tag

An independent, reproducible pipeline for a GeoParquet dataset of OpenStreetMap
area features carrying raw image-reference tags.

The project reads PBF input from
`/Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw` and reserves
`/Volumes/Seagate M3/projects/osm-polygon-image-tag` for generated state. Input
PBFs are immutable. Phase 1 provides only a read-only preflight:

```bash
uv run osm-polygon-image-tag preflight \
  --source-root "/Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw" \
  --data-root "/Volumes/Seagate M3/projects/osm-polygon-image-tag"
```

Construct or resume locally:

```bash
uv run osm-polygon-image-tag run \
  --source-root "/Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw" \
  --data-root "/Volumes/Seagate M3/projects/osm-polygon-image-tag"
```

Publish verified existing artifacts, or construct and publish after each PBF:

```bash
uv run osm-polygon-image-tag publish \
  --source-root "/Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw" \
  --data-root "/Volumes/Seagate M3/projects/osm-polygon-image-tag" \
  --confirm-repo NoeFlandre/osm-polygon-image-tag

uv run osm-polygon-image-tag run-and-publish \
  --source-root "/Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw" \
  --data-root "/Volumes/Seagate M3/projects/osm-polygon-image-tag" \
  --confirm-repo NoeFlandre/osm-polygon-image-tag
```

Authenticate first with `hf auth login`. Publication sends only verified
GeoParquet shards, their manifests, exact statistics, and the generated dataset
card. It verifies every changed remote file before atomically recording a local
receipt. `SIGINT` or `SIGTERM` finishes the current PBF boundary; rerunning
reuses verified shards and publication receipts.

No provider APIs are called and no images are downloaded.

Selected references include `image`, `wikimedia_commons`, `mapillary`,
`panoramax`, numeric indexed keys such as `panoramax:0`, `kartaview`, `flickr`,
and Bing Streetside `bubbleid`. Indexed Panoramax values are stored with their
original keys in the non-null `panoramax_values` map; `bubbleid` has a dedicated
nullable column, and every original OSM tag remains in `tags`.

Dataset schema and processing contract version 2 introduced these fields.
Resuming a version-1 data root safely rebuilds its old shards before publication.

## License

Pipeline code is Apache-2.0. OpenStreetMap-derived data remains subject to the
Open Database License; the generated dataset card records attribution and
factual statistics.
