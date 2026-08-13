# Data contract

The managed root keeps lossless per-PBF inputs for resumability. Hugging Face
publication uses a separate deterministic `public/` view: one polygon row per
`osm_type` + `osm_id` + `osm_version`, with all contributing PBF names in
`source_pbfs`, and image assets remapped to the selected source identity.

The pipeline produces one GeoParquet shard per source PBF. Every row is one
observation of one OSM object in one source PBF; overlapping Geofabrik
extracts remain lossless and are accounted for in the global statistics.

## Selected tag set

A polygon or multipolygon observation is emitted when the emitting way or
relation itself contains at least one non-empty value for any of:

- `image`
- `wikimedia_commons`
- `mapillary`
- `panoramax`
- `panoramax:<n>` where `<n>` is one or more ASCII digits
- `kartaview`
- `flickr`
- `bubbleid` (Bing Streetside)

Keys that look similar but are not selected include `panoramax:left`,
`panoramax:` with no suffix, `panoramax:1:foo`, `image:license`, and
`contact:flickr`. The full selection logic lives in
`osm_polygon_image_tag.ingest.extraction.is_target_tag_key`.

## GeoParquet schema

Schema version 3 (current). Bumping the schema or processing contract
in `osm_polygon_image_tag.core.manifest` invalidates every previously built
shard; the next run rebuilds them deterministically under the new contract.

| Column | Type | Null | Meaning |
| --- | --- | ---: | --- |
| `osm_type` | string | no | `way` or `relation` |
| `osm_id` | int64 | no | OSM object ID |
| `osm_version` | int32 | yes | OSM version when present |
| `osm_changeset` | int64 | yes | OSM changeset when present |
| `osm_timestamp` | timestamp UTC | yes | OSM timestamp when present |
| `source_pbf` | string | no | Stable relative source path |
| `source_feature_id` | string | no | Deterministic identity |
| `geometry` | binary | no | CRS84 Polygon/MultiPolygon WKB |
| `geometry_type` | string | no | `Polygon` or `MultiPolygon` |
| `area_m2` | float64 | no | Geodesic square metres |
| `bbox_min_lon` | float64 | no | Western bound |
| `bbox_min_lat` | float64 | no | Southern bound |
| `bbox_max_lon` | float64 | no | Eastern bound |
| `bbox_max_lat` | float64 | no | Northern bound |
| `tags` | list<struct<key:string,value:string>> | no | Every original OSM tag, sorted by key |
| `image` | string | yes | Exact raw value of `image` |
| `wikimedia_commons` | string | yes | Exact raw value |
| `mapillary` | string | yes | Exact raw value |
| `panoramax` | string | yes | Exact raw value of `panoramax` |
| `panoramax_values` | list<struct<key:string,value:string>> | no | Exact `panoramax` plus indexed entries, sorted by key |
| `kartaview` | string | yes | Exact raw value |
| `flickr` | string | yes | Exact raw value |
| `bubbleid` | string | yes | Exact raw value of `bubbleid` |

The public polygon file also contains `source_pbfs` (`list<string>`), sorted
source-PBF names for the feature. The canonical `source_pbf` is the
lexicographically first source, which makes joins and releases deterministic.

GeoParquet metadata:

- version `1.1.0`
- primary column `geometry`
- geometry encoding `WKB`
- geometry types `Polygon`, `MultiPolygon`
- coordinate reference system `OGC:CRS84`

## Manifest contract

Every shard has a sibling `*.manifest.json` with the following shape:

```json
{
  "manifest_schema_version": 1,
  "processing_contract_version": 2,
  "dataset_schema_version": 3,
  "source": {
    "relative_path": "europe/france.osm.pbf",
    "size_bytes": 1234567890,
    "mtime_ns": 1234567890,
    "sha256": "..."
  },
  "output": {
    "relative_path": "data/europe-france-osm-pbf-039882d43dbd.parquet",
    "size_bytes": 12345,
    "sha256": "...",
    "row_count": 42
  },
  "osmium_version": "osmium version 1.19.1",
  "counts": {
    "accepted_rows": 42,
    "rejections": { "non_polygon_geometry": 1 }
  }
}
```

`processing_contract_version` and `dataset_schema_version` must match the
current constants in `core.manifest` for the manifest to be reused during
fast resume.

## Image asset configuration

Asset schema version 1 is a separate, one-to-many Parquet configuration.
Rows preserve the exact source reference and factual resolution result; no
image body is downloaded. Join to polygons on `osm_type`, `osm_id`,
`osm_version`, and `source_pbf`.

Published asset rows use `source_polygon_shard = public/polygons.parquet` and
the canonical polygon `source_pbf`. Duplicate asset rows are removed by
feature identity, provider reference, asset index, and returned URL fields.

| Columns | Type / meaning |
| --- | --- |
| `source_pbf`, `source_polygon_shard` | non-null string provenance |
| `osm_type`, `osm_id`, `osm_version` | object identity |
| `provider`, `source_tag_key`, `source_tag_value` | exact source reference |
| `canonical_reference`, `provider_asset_id`, `asset_index` | provider identity and stable one-to-many index |
| `relation_kind` | direct reference or Commons category membership |
| `page_url`, `image_url`, `thumbnail_url` | factual resolved URLs |
| `image_url_expires_at` | nullable UTC expiry |
| `mime_type`, `width`, `height` | nullable returned/probed metadata |
| `license_id`, `license_url`, `author` | nullable metadata; absence makes no licensing claim |
| `status`, `reason`, `category_truncated`, `retry_after` | outcome and retry state |
| `resolver_contract_version`, `response_sha256` | resolver/cache provenance |

Statuses are `resolved`, `resolved_page_only`, `not_direct_image`,
`category_empty`, `category_truncated`, `not_found`, `private`,
`requires_auth`, `invalid_reference`, `unsupported`, and
`temporary_failure`. Commons category expansion is capped at 500 members and
uses `category_membership`, which does not assert depiction. Mapillary and
Flickr return page-only results without `MAPILLARY_ACCESS_TOKEN` and
`FLICKR_API_KEY`. Bing Streetside is page-only because this project uses no
documented raw-image API for it.

Expiring direct URLs refresh when they enter a one-hour refresh window.
Provider cooldowns and temporary failures remain retryable rather than making
a shard permanently reusable.

Unsafe network targets are a permanent policy rejection, not a transient
provider failure. If DNS resolves a host to a non-public address (including
when the transport wraps that policy error), the resolver records
`status="invalid_reference"` with `reason="unsafe_url"`, emits an
`asset_provider_blocked` progress event, and performs no retry. The validated
connection is never opened.

Asset manifests record cache hits and provider resolver requests. Global
statistics and the generated card aggregate those factual counts without
consulting live provider state.

Each `*.assets.parquet` has an atomic `*.assets.manifest.json` containing the
polygon identity, asset/resolver versions, output identity, counts, and a
digest of only cache records used by that shard. Unrelated cache writes cannot
invalidate a completed asset shard.

### Non-cacheable secret-bearing references

Source-provided URLs whose query contains a secret-like key (`access_token`,
`api_key`, `token`, or `key`, matched case-insensitively and after
percent-decoding) are treated as non-cacheable. Such a reference is resolved
once against the original request URL so direct image resolution is preserved,
but its `ResolutionRecord` is never written to the SQLite resolution cache and
its `ResolutionKey` is never added to the shard resolution snapshot. This
prevents durable cache persistence of secret material while allowing the
pipeline to continue instead of aborting the shard or the run. A shard that
contains only non-cacheable references finalizes normally with a zero-entry
resolution snapshot.

The existing strict rejection in `validate_resolution_record` remains in force
for every durable cache write, so a non-cacheable reference can never reach the
cache or snapshot even through other code paths.

Resolution cache reads are fail-closed: a digest mismatch or malformed durable
payload raises `ResolutionCacheError` rather than leaking a raw JSON, key, or
timestamp parsing exception. The pipeline never silently repairs or discards a
corrupt cache row; the error remains available to the operator for recovery.

Derived asset URLs retain source query parameters. The resolver is handed the
original request URL verbatim and every URL it returns is recorded faithfully,
because rewriting or stripping query parameters would discard provenance and
could silently alter the factual resolution result. The security boundary is
the durable resolution cache and snapshot: those surfaces never persist
secret-bearing references, while the asset shard preserves exact source
provenance exactly as it appears in OpenStreetMap. Original OSM tags and the
polygon GeoParquet shards are read-only and remain untouched.

Resume rebuilds a shard solely from the finalized polygon Parquet and the
existing resolution cache; completed PBFs are never reopened or reprocessed. A
previously interrupted shard rebuilds deterministically on the next run.

## Global statistics

`generate_metadata` produces `statistics/dataset-statistics.json` and
`README.md` in the data root. Statistics include polygon and asset shard/row counts,
`osm_types` and `geometry_types` counts, per-provider counts, exact
provider combinations, minimum/maximum feature timestamps, sum/min/max/mean
`area_m2`, exact rejection counts by reason, exact duplicate-observation
counts across PBFs, per-shard digests, and a `geography` block describing
the generated H3 polygon-density map. The README card mirrors these
statistics and includes OpenStreetMap/Geofabrik attribution and ODbL
licensing.

## Geographic coverage map

`generate_metadata` also renders a static PNG `assets/geographic_polygon_density.png`
that visualizes the raw count of finalized `polygons` rows per H3 cell. The
map is built exclusively from finalized polygon GeoParquet shards; the raw
PBF tree is never opened during metadata generation.

- Each polygon row contributes exactly once, computed from its Shapely
  geometry centroid in `OGC:CRS84`. The bounding-box midpoint is not used.
- H3 resolution is fixed at `3` (the dataset-card artifact contract).
- Overlapping Geofabrik extracts are preserved as separate observations,
  so two shards covering the same OSM feature count it twice.
- The colour scale is logarithmic (`magma`); raw counts per cell range
  from the minimum to the maximum finalized polygon row count.
- `image_assets` rows are not separately counted in this map.
- The basemap is a bundled Natural Earth 1:110m landmass reference
  (public domain) shipped with the package; no network call is performed
  during map generation.
- The map is omitted as a separate PNG from the GeoParquet schema: centroids
  are recomputed transiently from each finalized shard's `geometry` column.

The generated `geography` block in `statistics/dataset-statistics.json`
exposes:

- `h3_resolution`, `cell_count`, `polygon_rows`,
  `min_cell_count`, `max_cell_count`, `input_shard_count`,
  `input_digest` (a deterministic SHA-256 over the finalized shard
  identities used to compute the map).

## Publication inventory

`publication_inventory` enumerates exactly:

- `README.md`
- `statistics/dataset-statistics.json`
- `assets/geographic_polygon_density.png` (the dataset-card map).
- Every GeoParquet shard whose manifest matches the current contract, plus
  the matching manifest.
- Every asset shard whose manifest matches the current asset/resolver
  contracts, plus its matching manifest.
- `cache/`, `catalog/`, and `receipts/` are internal and never uploaded.

The geographic PNG is treated as a required, validated publication
artifact: it must be a non-empty regular PNG file inside the data root,
never a symlink or path escape. A missing or corrupt PNG fails the
inventory with a `PublicationError`.

The private `cache/geographic-density/` directory holds the per-shard
H3 cell cache and the per-shard input digest metadata used to short
circuit regenerations during repeated metadata runs. It is never
included in the publication inventory.

Symlinks, top-level escapes, and unexpected files fail closed with a
`PublicationError`.
