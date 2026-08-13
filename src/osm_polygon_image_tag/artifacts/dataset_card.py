"""Render the deterministic Hugging Face dataset card."""

import json
import re
from collections.abc import Mapping
from typing import Any

import yaml

from osm_polygon_image_tag.artifacts.geography.render import GEOGRAPHIC_PNG_RELATIVE
from osm_polygon_image_tag.artifacts.hero import HERO_PNG_RELATIVE

_SECRET_QUERY = re.compile(
    r"(?i)(access_token|api_key|apikey|key|token|secret|signature)=([^&#\s]+)"
)


def _count(value: Any) -> str:
    """Format a statistics count for people without changing the JSON stats."""
    return f"{int(value):,}"


def _safe_example_value(value: object) -> object:
    """Make an example JSON-safe while redacting secret-like query values."""
    if isinstance(value, bytes):
        return f"0x{value.hex()}"
    if isinstance(value, str):
        return _SECRET_QUERY.sub(r"\1=[redacted]", value)
    if isinstance(value, Mapping):
        return {str(key): _safe_example_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_example_value(item) for item in value]
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return value


def _example_json(example: Mapping[str, object] | None) -> str:
    """Render one deterministic example row, or a clear empty-state sentence."""
    if example is None:
        return "No finalized row is available in this snapshot."
    rendered = json.dumps(_safe_example_value(example), ensure_ascii=False, indent=2)
    return f"```json\n{rendered}\n```"


def dataset_card(
    statistics: dict[str, Any],
    *,
    examples: Mapping[str, Mapping[str, object] | None] | None = None,
) -> bytes:
    """Render a card containing only facts derived from the statistics payload."""
    providers = "\n".join(
        f"- `{provider}`: {_count(count)}"
        for provider, count in statistics["provider_counts"].items()
    )
    metadata = {
        "license": "odbl",
        "tags": ["openstreetmap", "geospatial", "geoparquet", "image"],
        "configs": [
            {
                "config_name": "polygons",
                "default": True,
                "data_files": [{"split": "train", "path": "public/polygons.parquet"}],
            },
            {
                "config_name": "image_assets",
                "data_files": [{"split": "train", "path": "public/image_assets.parquet"}],
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
    example_values = examples if examples is not None else {}
    text = f"""---\n{frontmatter}---
![OSM Polygon Image Tag hero]({HERO_PNG_RELATIVE})

# OSM Polygon Image Tag

This dataset contains OpenStreetMap Polygon and MultiPolygon observations whose
way or relation carries at least one raw image-reference tag.

## Current verified contents

- Source PBFs processed: {_count(statistics["shards"])}
- Unique OSM features published: {_count(statistics["rows"])}
- Duplicate rows removed from the public view:
  {_count(statistics.get("duplicate_observations_removed", 0))}
- Image-reference rows checked: {_count(assets["rows"])}
- Rows with a directly usable image URL: {_count(assets["direct_urls"])}
- Rows with a non-expiring direct image URL: {_count(assets["stable_direct_urls"])}
- Rows with a provider page URL (not necessarily an image): {_count(assets["page_urls"])}
- Resolver results reused from cache: {_count(assets["cache_hits"])}
- Provider lookups performed: {_count(assets["network_resolutions"])}

Provider observations:
{providers}

## Geographic coverage

### OSM polygon density

![Geographic OSM Polygon Density]({GEOGRAPHIC_PNG_RELATIVE})

Each colored H3 cell contains the raw count of finalized `polygons` rows
whose geometry centroid falls inside the cell. Every polygon row
contributes exactly once, computed from its Shapely geometry centroid
in CRS84 (not its bounding-box midpoint). The map is built at H3 resolution
{h3_resolution}. A duplicate is the same `osm_type`, `osm_id`, and
`osm_version` found in more than one source PBF. The public view keeps one
deterministic row (the lexicographically first source PBF) and records every
source in `source_pbfs`; internal per-PBF shards remain private for resume and
audit. The colour scale
is logarithmic (`magma`); raw counts per-cell range from
{_count(min_cell_count)} to {_count(max_cell_count)} across {_count(cell_count)} H3
cells, with {_count(polygon_rows)} finalized polygon rows from
{_count(input_shard_count)} source PBF(s). The `image_assets` configuration is
not separately counted in this map.

### Duplicate policy

`Duplicate rows removed from the public view` counts the extra copies after
grouping by `osm_type` + `osm_id` + `osm_version`; two copies count as one
removed row. The selected row's `source_pbfs` list preserves all source-PBF
provenance. `image_assets` rows are remapped to that selected source and
deduplicated by feature, provider, reference, and asset index.

## Schema

Rows include OSM type/ID/version/changeset/timestamp, source PBF identity, full
OGC:CRS84 WKB geometry, geodesic `area_m2`, bounds, every original OSM tag, and
the exact raw `image`, `wikimedia_commons`, `mapillary`, `panoramax`,
`panoramax_values`, `kartaview`, `flickr`, and `bubbleid` values.
The map-like `tags` and `panoramax_values` fields are deterministic lists of
`{{"key": ..., "value": ...}}` objects so the configuration is directly
readable by the Hugging Face Dataset Viewer.

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

## Example rows

These are real rows from this snapshot. They show every column exposed by the
corresponding configuration. Geometry is encoded as lowercase WKB hex; any
secret-like URL query value is redacted here for safe documentation.

### `polygons`

{_example_json(example_values.get("polygon"))}

### `image_assets`

{_example_json(example_values.get("asset"))}

## Code

The pipeline that builds this dataset is maintained in the
[NoeFlandre/osm-polygon-image-tag GitHub repository](https://github.com/NoeFlandre/osm-polygon-image-tag).

## Dataset metrics

The companion [Trackio metrics Space](https://huggingface.co/spaces/NoeFlandre/osm-polygon-image-tag-trackio)
shows the current dataset counts, image-URL coverage, duplicate removal, and
geographic summary. It records one data-derived snapshot per published update.

This snapshot is versioned as [GitHub release v0.1.0](https://github.com/NoeFlandre/osm-polygon-image-tag/releases/tag/v0.1.0).

## Provenance and license

Source extracts are provided by Geofabrik from OpenStreetMap. OpenStreetMap data
is available under the Open Database License. Attribution: © OpenStreetMap
contributors.

## Citation

If you use this dataset, please cite **Noé Flandre, OSM Polygon Image Tag,
version 0.1.0**. The machine-readable citation metadata is available in the
[dataset's `citation.cff`](citation.cff) and the [project's `citation.cff`](https://github.com/NoeFlandre/osm-polygon-image-tag/blob/main/citation.cff).

## Limitations and intended use

References and resolved URLs may be stale, inaccessible, provider-specific, or
unrelated to the current feature. Inclusion does not establish image copyright,
licensing, safety, availability, or correspondence to the mapped feature.
Wikimedia Commons category membership does not prove depiction of the polygon;
it is labeled as `category_membership`. No image body is downloaded.

Internal per-PBF artifacts are retained privately for resumable processing and
audit. The published configurations are the deduplicated `public/` view.
Statistics in this card are generated only from finalized, size-checked public
GeoParquet and asset shards. (See the generated statistics file for exact counts.)
The geographic coverage map references Natural Earth 1:110m landmass data
(public domain) for context only.
"""
    return text.encode("utf-8")
