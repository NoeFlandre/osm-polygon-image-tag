# Data contract

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

Schema version 2 (current). Bumping the schema or processing contract
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
| `tags` | map<string,string> | no | Every original OSM tag |
| `image` | string | yes | Exact raw value of `image` |
| `wikimedia_commons` | string | yes | Exact raw value |
| `mapillary` | string | yes | Exact raw value |
| `panoramax` | string | yes | Exact raw value of `panoramax` |
| `panoramax_values` | map<string,string> | no | Exact `panoramax` plus indexed entries |
| `kartaview` | string | yes | Exact raw value |
| `flickr` | string | yes | Exact raw value |
| `bubbleid` | string | yes | Exact raw value of `bubbleid` |

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
  "dataset_schema_version": 2,
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

## Global statistics

`generate_metadata` produces `statistics/dataset-statistics.json` and
`README.md` in the data root. The statistics include shard and row counts,
`osm_types` and `geometry_types` counts, per-provider counts, exact
provider combinations, minimum/maximum feature timestamps, sum/min/max/mean
`area_m2`, exact rejection counts by reason, exact duplicate-observation
counts across PBFs, and per-shard digests. The README card mirrors these
statistics and includes OpenStreetMap/Geofabrik attribution and ODbL
licensing.

## Publication inventory

`publication_inventory` enumerates exactly:

- `README.md`
- `statistics/dataset-statistics.json`
- Every GeoParquet shard whose manifest matches the current contract, plus
  the matching manifest.
- `catalog/catalog.sqlite` and `receipts/publication.json` are internal and
  are never uploaded.

Symlinks, top-level escapes, and unexpected files fail closed with a
`PublicationError`.
