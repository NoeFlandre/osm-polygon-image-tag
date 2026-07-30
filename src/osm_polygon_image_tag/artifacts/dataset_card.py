"""Render the deterministic Hugging Face dataset card."""

from typing import Any

import yaml


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
- Page URLs: {assets["page_urls"]}

Provider observations:
{providers}

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
"""
    return text.encode("utf-8")
