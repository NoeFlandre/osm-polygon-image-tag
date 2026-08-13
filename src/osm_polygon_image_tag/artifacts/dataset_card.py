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

This dataset connects OpenStreetMap polygons to image references found in their
tags. It contains closed ways and relations only. A polygon row is one unique
OSM feature. An image-asset row describes one image reference or one lookup
result for a feature.

## Snapshot summary

This snapshot contains:

- Source PBF files processed: {_count(statistics["shards"])}
- Published OSM features: {_count(statistics["rows"])}
- Repeated feature rows removed: {_count(statistics.get("duplicate_observations_removed", 0))}
- Image-reference rows checked: {_count(assets["rows"])}
- Rows with a usable image URL: {_count(assets["direct_urls"])}
- Rows with a non-expiring image URL: {_count(assets["stable_direct_urls"])}
- Rows with a provider page URL (a page, not necessarily an image): {_count(assets["page_urls"])}
- Cached lookups reused: {_count(assets["cache_hits"])}
- New provider lookups: {_count(assets["network_resolutions"])}

The counts below show how many image references came from each source tag:
{providers}

## Where the features are

### Polygon density map

![Geographic OSM Polygon Density]({GEOGRAPHIC_PNG_RELATIVE})

Each colored H3 cell shows the number of published polygon rows in that cell.
We place a polygon in the cell containing its geometry's center, so every
polygon is counted once. The map uses H3 resolution {h3_resolution}, a global
grid system. Colors use a logarithmic scale (`magma`) so sparse and dense areas
are both visible. Cell counts range from {_count(min_cell_count)} to
{_count(max_cell_count)} across {_count(cell_count)} cells. The map contains
{_count(polygon_rows)} published polygon rows from {_count(input_shard_count)}
source PBF files. Image-asset rows are not counted separately.

### How repeated rows are removed

The same OSM feature can appear in more than one source PBF file. We call rows
duplicates when they have the same OSM type, ID, and version. For each group:

1. We keep one copy, chosen by a fixed rule so the result is reproducible.
2. We list every source file in `source_pbfs`, so the provenance is not lost.
3. We point the feature's image rows to the kept copy and remove repeated image
   rows with the same feature, source, reference, and asset index.

The private per-PBF files remain available for resume and audit; the public files
contain only this deduplicated view.

## What is in the files

### `polygons`

Each row contains the OSM type, ID, version, changeset, timestamp, source
information, geometry, area in square metres, bounding box, every original OSM
tag, and the raw image-reference values: `image`, `wikimedia_commons`,
`mapillary`, `panoramax`, `kartaview`, `flickr`, and `bubbleid`.

`geometry` is compact WKB geometry in CRS84. `tags` and `panoramax_values` are
lists of `{{"key": ..., "value": ...}}` objects, which makes every original
tag easy to read in the Dataset Viewer. `source_pbfs` lists all source PBF files
that contained the feature.

### `image_assets`

This file can contain several rows for one polygon. Each row keeps the original
tag and value, the source name, the provider's reference, the lookup status,
the provider page URL, a direct image URL when one was found, expiration time,
file type, dimensions, and license/author information when available.

Join the two files with `osm_type`, `osm_id`, and `osm_version`. In the public
view, `source_pbf` identifies the selected polygon copy; `source_pbfs` contains
the complete source history.

```python
from datasets import load_dataset

polygons = load_dataset("NoeFlandre/osm-polygon-image-tag", "polygons")
image_assets = load_dataset("NoeFlandre/osm-polygon-image-tag", "image_assets")
```

## Example rows

These are real rows from this snapshot and show every column in each file.
Geometry is shown as WKB hex (a compact binary format). Secret-like URL query
values are redacted in this documentation example.

### `polygons`

{_example_json(example_values.get("polygon"))}

### `image_assets`

{_example_json(example_values.get("asset"))}

## Code and metrics

The pipeline is maintained in the
[OSM Polygon Image Tag GitHub repository](https://github.com/NoeFlandre/osm-polygon-image-tag).

The [Trackio metrics Space](https://huggingface.co/spaces/NoeFlandre/osm-polygon-image-tag-trackio)
shows the current row counts, image-URL coverage, duplicate removal, and map
summary. It stores one data-derived snapshot for each published update.

This snapshot is versioned as [GitHub release v0.1.0](https://github.com/NoeFlandre/osm-polygon-image-tag/releases/tag/v0.1.0).

## Source, license, and citation

The source extracts come from Geofabrik's OpenStreetMap extracts. OpenStreetMap
data is available under the Open Database License. Attribution: © OpenStreetMap
contributors.

If you use this dataset, cite **Noé Flandre, OSM Polygon Image Tag, version
0.1.0**. The machine-readable citation is in the [dataset's
`citation.cff`](citation.cff) and the [project's `citation.cff`](https://github.com/NoeFlandre/osm-polygon-image-tag/blob/main/citation.cff).

## Limitations

An image reference or URL can be stale, inaccessible, provider-specific, or
unrelated to the mapped feature. A URL is not a guarantee that an image can be
downloaded today. Inclusion does not establish copyright, license, safety, or
that the image depicts the feature. A Wikimedia Commons category can contain
many unrelated files; those rows are marked `category_membership`. The pipeline
does not download image bodies.

Statistics in this card are generated only from finalized, size-checked public
GeoParquet and asset shards. (See the generated statistics file for exact counts.)
The map uses Natural Earth 1:110m landmass data (public domain) for context.
"""
    return text.encode("utf-8")
