"""Render the deterministic Hugging Face dataset card."""

from typing import Any

import yaml

from osm_polygon_image_tag.artifacts.geography.render import GEOGRAPHIC_PNG_RELATIVE


def dataset_card(statistics: dict[str, Any]) -> bytes:
    """Render a card containing only facts derived from the statistics payload."""
    providers = "\n".join(
        f"- `{provider}`: {count}" for provider, count in statistics["provider_counts"].items()
    )
    metadata = {
        "license": "odbl",
        "tags": ["openstreetmap", "geospatial", "geoparquet", "image"],
        "configs": [
            {
                "config_name": "polygons",
                "default": True,
                "data_files": [{"split": "train", "path": "data/*.parquet"}],
            },
            {
                "config_name": "image_assets",
                "data_files": [{"split": "train", "path": "assets/*.assets.parquet"}],
            },
        ],
    }
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    assets = statistics["assets"]
    geography = statistics.get("geography") or {}
    polygon_rows = statistics["rows"]
    h3_resolution = int(geography.get("h3_resolution") or 3)
    cell_count = int(geography.get("cell_count") or 0)
    min_cell_count = int(geography.get("min_cell_count") or 0)
    max_cell_count = int(geography.get("max_cell_count") or 0)
    input_shard_count = int(geography.get("input_shard_count") or 0)
    text = f"""---\n{frontmatter}---
# OSM Polygon Image Tag

This dataset contains OpenStreetMap Polygon and MultiPolygon observations whose
way or relation carries at least one raw image-reference tag.

## Current verified contents

- Shards: {statistics["shards"]}
- Rows: {statistics["rows"]}
- Duplicate observations across source PBFs: {statistics["duplicate_observations"]}
- Resolved asset rows: {assets["rows"]}
- Direct image URLs: {assets["direct_urls"]}
- Stable direct image URLs: {assets["stable_direct_urls"]}
- Page URLs: {assets["page_urls"]}
- Resolution cache hits: {assets["cache_hits"]}
- Provider resolver requests: {assets["network_resolutions"]}

Provider observations:
{providers}

## Geographic coverage

### OSM polygon density

![Geographic OSM Polygon Density]({GEOGRAPHIC_PNG_RELATIVE})

Each colored H3 cell contains the raw count of finalized `polygons` rows
whose geometry centroid falls inside the cell. Every polygon row
contributes exactly once, computed from its Shapely geometry centroid
in CRS84 (not its bounding-box midpoint). The map is built at H3 resolution
{h3_resolution}. Overlapping Geofabrik extracts are
intentionally preserved as separate observations, so two shards
covering the same OSM feature will count it twice. The colour scale
is logarithmic (`magma`); raw counts per-cell range from
{min_cell_count:,} to {max_cell_count:,} across {cell_count:,} H3
cells, with {polygon_rows:,} finalized polygon rows from
{input_shard_count:,} shard(s). The `image_assets` configuration is
not separately counted in this map.

## Schema

Rows include OSM type/ID/version/changeset/timestamp, source PBF identity, full
OGC:CRS84 WKB geometry, geodesic `area_m2`, bounds, every original OSM tag, and
the exact raw `image`, `wikimedia_commons`, `mapillary`, `panoramax`,
`panoramax_values`, `kartaview`, `flickr`, and `bubbleid` values.

The `image_assets` configuration is one-to-many. Join it to `polygons` with
`osm_type`, `osm_id`, `osm_version`, and `source_pbf`. Asset rows preserve the
exact source key/value, canonical provider reference, resolution status, page
URL, direct image URL when returned, expiry, MIME type, dimensions, and
structured license/author metadata when available.

```python
from datasets import load_dataset

polygons = load_dataset("NoeFlandre/osm-polygon-image-tag", "polygons")
image_assets = load_dataset("NoeFlandre/osm-polygon-image-tag", "image_assets")
```

## Provenance and license

Source extracts are provided by Geofabrik from OpenStreetMap. OpenStreetMap data
is available under the Open Database License. Attribution: © OpenStreetMap
contributors.

## Limitations and intended use

References and resolved URLs may be stale, inaccessible, provider-specific, or
unrelated to the current feature. Inclusion does not establish image copyright,
licensing, safety, availability, or correspondence to the mapped feature.
Wikimedia Commons category membership does not prove depiction of the polygon;
it is labeled as `category_membership`. No image body is downloaded.

Overlapping Geofabrik extracts are intentionally preserved as separate
observations and quantified above. Statistics in this card are generated only
from finalized manifests and their size-checked GeoParquet and asset shards.
The geographic coverage map references Natural Earth 1:110m landmass data
(public domain) for context only.
"""
    return text.encode("utf-8")
