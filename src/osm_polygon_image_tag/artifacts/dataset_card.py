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


def _percentage(part: Any, total: Any) -> str:
    """Format a count as a one-decimal percentage of a total."""
    denominator = int(total)
    if denominator <= 0:
        return "0.0%"
    return f"{int(part) / denominator * 100:.1f}%"


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
                "config_name": "images",
                "data_files": [{"split": "train", "path": "public/images.parquet"}],
            },
            {
                "config_name": "polygon_images",
                "data_files": [{"split": "train", "path": "public/polygon_images.parquet"}],
            },
        ],
    }
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    assets = statistics["assets"]
    image_relation_counts = assets.get("image_relation_counts", {})
    direct_image_rows = int(image_relation_counts.get("direct_reference", 0))
    indirect_image_rows = int(image_relation_counts.get("category_membership", 0))
    usable_relationship_rows = int(assets.get("usable_relationship_rows", 0))
    direct_image_summary = (
        f"- Directly linked from an OSM tag: {_count(direct_image_rows)} "
        f"({_percentage(direct_image_rows, usable_relationship_rows)})"
    )
    indirect_image_summary = (
        "- Indirectly reached through a Wikimedia Commons category: "
        f"{_count(indirect_image_rows)} "
        f"({_percentage(indirect_image_rows, usable_relationship_rows)})"
    )
    usable_link_summary = (
        f"The next two percentages use {_count(usable_relationship_rows)} links "
        "with a usable direct image URL as the denominator."
    )
    page_url_summary = (
        "- Unique images with a provider page URL (a page, not necessarily an image): "
        f"{_count(assets['page_urls'])}"
    )
    geography = statistics.get("geography") or {}
    polygon_rows = statistics["rows"]
    source_pbf_count = int(statistics["shards"])
    source_pbf_phrase = (
        f"the {_count(source_pbf_count)} processed source PBF file"
        if source_pbf_count == 1
        else f"all {_count(source_pbf_count)} processed source PBF files"
    )
    h3_resolution = int(geography.get("h3_resolution") or 3)
    cell_count = int(geography.get("cell_count") or 0)
    min_cell_count = int(geography.get("min_cell_count") or 0)
    max_cell_count = int(geography.get("max_cell_count") or 0)
    map_summary = (
        f"The map contains {_count(polygon_rows)} published polygon rows from {source_pbf_phrase}."
    )
    example_values = examples if examples is not None else {}
    text = f"""---\n{frontmatter}---
![OSM Polygon Image Tag hero]({HERO_PNG_RELATIVE})

# OSM Polygon Image Tag

This dataset connects OpenStreetMap polygons to image references found in their
tags. It contains closed ways and relations only.

The release has three files:

- `polygons`: one current row per OSM type and ID, including its geometry and
  all original tags.
- `images`: one row for each unique provider image. It keeps the provider's
  reference and, when available, a direct image URL.
- `polygon_images`: the links between features and images. This is a separate
  file. One image can be linked to many features, and one feature can have many images.

A tag value is the original reference. It may be a URL, provider ID, UUID, or
category name; it is not always an image URL. The `images` file records the
lookup result. Use `polygons` for the original OSM tags and `images` for the
lookup result.

## Snapshot summary

This snapshot contains:

- Source PBF files processed: {_count(statistics["shards"])}
- Published OSM features: {_count(statistics["rows"])}
- Duplicate polygon rows removed: {_count(statistics.get("duplicate_observations_removed", 0))}
- Unique image records: {_count(assets["rows"])}
- Image-reference links checked: {_count(assets.get("relationship_rows", 0))}
- Unique images with a usable direct image URL: {_count(assets["direct_urls"])}
- Links with a usable direct image URL: {_count(usable_relationship_rows)}
- Duplicate image records removed: {_count(assets.get("duplicate_images_removed", 0))}
- Duplicate polygon-to-image links removed: {_count(assets.get("duplicate_links_removed", 0))}

These percentages count polygon-to-image links, not unique images. A single
image can be linked to more than one polygon.

{usable_link_summary}

{direct_image_summary}
{indirect_image_summary}
- Unique images with a non-expiring image URL: {_count(assets["stable_direct_urls"])}
{page_url_summary}
- Cached lookups reused: {_count(assets["cache_hits"])}
- New provider lookups: {_count(assets["network_resolutions"])}

The source-tag counts below are counts of polygons carrying each tag, not image
counts.
{providers}

## Where the features are

### Polygon density map

![Geographic OSM Polygon Density]({GEOGRAPHIC_PNG_RELATIVE})

Each colored H3 cell shows the number of published polygon rows in that cell.
We place a polygon in the cell containing its geometry's center, so every
polygon is counted once. The map uses H3 resolution {h3_resolution}, a global
grid system. Colors use a logarithmic scale (`magma`) so sparse and dense areas
are both visible. Cell counts range from {_count(min_cell_count)} to
{_count(max_cell_count)} across {_count(cell_count)} cells. {map_summary} Image
rows and links are not counted on this map.

### How repeated rows are removed

The same OSM feature can appear in more than one source PBF file, and an object
can have several historical versions. The public `polygons` file keeps one row per OSM type and ID.
Rows with the same OSM type, ID, and version are duplicates. The newest version
(or newest timestamp when no version is available) wins.

1. We keep one copy, chosen by a fixed rule so the result is reproducible.
2. We list every source file in `source_pbfs`, so the provenance is not lost.
3. We deduplicate `images` by provider and physical image identity. A usable
   image URL is preferred; provider ID, reference, or page URL is used when no
   image URL exists.
4. We keep each distinct feature-to-image tag relationship in
   `polygon_images`, merging its source files and observed OSM versions.

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

### `images`

Each row describes one unique provider image. It keeps the provider's reference,
lookup status, provider page URL, direct image URL when one was found, expiration
time, file type, dimensions, and license/author information when available.

### `polygon_images`

Each row connects one polygon to one image and keeps the original tag key/value,
reference, relation type, source files, and observed OSM versions. Join it to
`polygons` with `osm_type` and `osm_id`, and to `images` with `image_id`.

```python
from datasets import load_dataset

polygons = load_dataset("NoeFlandre/osm-polygon-image-tag", "polygons")
images = load_dataset("NoeFlandre/osm-polygon-image-tag", "images")
polygon_images = load_dataset("NoeFlandre/osm-polygon-image-tag", "polygon_images")
```

## Example rows

These are real rows from this snapshot and show every column in each file.
Geometry is shown as WKB hex (a compact binary format). Secret-like URL query
values are redacted in this documentation example.

### `polygons`

{_example_json(example_values.get("polygon"))}

### `images`

{_example_json(example_values.get("image"))}

### `polygon_images`

{_example_json(example_values.get("polygon_image"))}

## Code and metrics

The pipeline is maintained in the
[OSM Polygon Image Tag GitHub repository](https://github.com/NoeFlandre/osm-polygon-image-tag).

The [Trackio metrics Space](https://huggingface.co/spaces/NoeFlandre/osm-polygon-image-tag-trackio)
shows the current row counts, image-URL coverage, duplicate removal, and map
summary. It stores one data-derived snapshot for each published update.

This snapshot is versioned as [GitHub release v0.1.0](https://github.com/NoeFlandre/osm-polygon-image-tag/releases/tag/v0.1.0).

## Source, license, and citation

The source extracts come from Geofabrik's OpenStreetMap extracts. OpenStreetMap
geometry, tags, and feature metadata are available under the Open Database License
(ODbL). Attribution: © OpenStreetMap contributors.

Image links are separate from the OSM data.
The provider terms and image license are separate from ODbL.
When available, the `images` row records this information in `license_id`
and `license_url`. A URL is not permission to copy, redistribute, or use the image.
Check the provider and the original image page before using an image.
This dataset does not download or relicense image files.

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
GeoParquet files. (See the generated statistics file for exact counts.)
The map uses Natural Earth 1:110m landmass data (public domain) for context.
"""
    return text.encode("utf-8")
