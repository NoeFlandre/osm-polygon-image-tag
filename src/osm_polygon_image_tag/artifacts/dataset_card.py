"""Render the deterministic Hugging Face dataset card."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import yaml

from osm_polygon_image_tag.artifacts.geography.render import GEOGRAPHIC_PNG_RELATIVE
from osm_polygon_image_tag.artifacts.hero import HERO_PNG_RELATIVE

REPO_ID = "NoeFlandre/osm-polygon-image-tag"
_CODE_URL = "https://github.com/NoeFlandre/osm-polygon-image-tag"
_TRACKIO_URL = "https://huggingface.co/spaces/NoeFlandre/osm-polygon-image-tag-trackio"
_ODBL_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
_RELEASE = "v0.1.1"

_QUICK_START_SQL = """```sql
SELECT p.osm_type, p.osm_id, p.area_m2, i.image_url, i.license_id
FROM 'hf://datasets/NoeFlandre/osm-polygon-image-tag/public/polygon_images.parquet' l
JOIN 'hf://datasets/NoeFlandre/osm-polygon-image-tag/public/polygons.parquet' p
  USING (osm_type, osm_id)
JOIN 'hf://datasets/NoeFlandre/osm-polygon-image-tag/public/images.parquet' i USING (image_id)
WHERE l.relation_kind = 'direct_reference' AND i.image_url IS NOT NULL
LIMIT 10;
```"""

_PROVIDER_TAGS = {
    "wikimedia_commons": "`wikimedia_commons`, `image=File:…` / `Category:…`",
    "image": "`image` (any other URL)",
    "mapillary": "`mapillary`",
    "panoramax": "`panoramax`, `panoramax:N`",
    "kartaview": "`kartaview`",
    "flickr": "`flickr`",
    "streetside": "`bubbleid`",
}

_STATUS_MEANINGS = {
    "resolved": "Direct image URL found",
    "resolved_page_only": (
        "Provider page only: no API credential (Mapillary, Flickr) "
        "or no raw-image API (Bing Streetside)"
    ),
    "not_found": "The provider or URL says the image does not exist",
    "not_direct_image": "The URL answers, but not with an `image/*` type (usually a web page)",
    "invalid_reference": "The tag value is not a valid reference for its provider",
    "category_empty": "Commons category with no file directly in it",
    "category_truncated": "Commons category over the 500-file cap whose kept files gave no image",
    "temporary_failure": "Provider error or rate limit; `retry_after` says when to retry",
}

_FIELD_HEADER = ("Field", "Type", "Description")

_POLYGON_FIELDS = (
    (
        "`osm_type`, `osm_id`",
        "string, int64",
        "OSM object (`way` or `relation`) and its ID; unique together",
    ),
    (
        "`osm_version`, `osm_changeset`, `osm_timestamp`",
        "int32, int64, timestamp",
        "Version kept (the newest seen) and its edit metadata",
    ),
    (
        "`geometry`",
        "WKB",
        "[GeoParquet](https://geoparquet.org) geometry, CRS84",
    ),
    ("`geometry_type`", "string", "`Polygon` or `MultiPolygon`"),
    ("`area_m2`", "float64", "Geodesic area on WGS 84, m²"),
    (
        "`bbox_min_lon`, `bbox_min_lat`, `bbox_max_lon`, `bbox_max_lat`",
        "float64",
        "Bounding box",
    ),
    ("`tags`", "list[{key, value}]", "All original OSM tags"),
    (
        "`image`, `wikimedia_commons`, `mapillary`, `kartaview`, `flickr`, `bubbleid`",
        "string",
        "Raw value of each image tag, or null",
    ),
    (
        "`panoramax`, `panoramax_values`",
        "string, list[{key, value}]",
        "`panoramax` tag, and every `panoramax` / `panoramax:N` tag",
    ),
    (
        "`source_pbf`, `source_feature_id`",
        "string",
        "Geofabrik extract of the kept row, and its row ID",
    ),
    ("`source_pbfs`", "list[string]", "Every extract that contained the polygon"),
)

_IMAGE_FIELDS = (
    ("`image_id`", "string", "Stable image ID (`img_` + SHA-256)"),
    (
        "`provider`",
        "string",
        "`wikimedia_commons`, `image`, `mapillary`, `panoramax`, `kartaview`, "
        "`flickr` or `streetside`",
    ),
    (
        "`canonical_reference`",
        "string",
        "Normalized reference that was looked up (file, category, URL, ID)",
    ),
    ("`provider_asset_id`", "string", "Provider's own image ID"),
    ("`page_url`", "string", "Provider page for the image"),
    ("`image_url`", "string", "Direct image URL; null when none was found"),
    ("`thumbnail_url`", "string", "Thumbnail URL, when the provider gives one"),
    ("`image_url_expires_at`", "timestamp", "When `image_url` expires; null if it does not"),
    ("`mime_type`, `width`, `height`", "string, int32", "Image type and size in pixels"),
    (
        "`license_id`, `license_url`, `author`",
        "string",
        "License and author as stated by the provider (`author` may be HTML)",
    ),
    (
        "`status`, `reason`",
        "string",
        "Lookup result and detail, see [Lookup status](#lookup-status)",
    ),
    ("`category_truncated`", "bool", "Came from a Commons category over the 500-file cap"),
    ("`retry_after`", "timestamp", "When a `temporary_failure` can be retried"),
    (
        "`resolver_contract_version`, `response_sha256`",
        "int32, string",
        "Lookup code version and digest of the provider response",
    ),
    ("`source_pbfs`", "list[string]", "Extracts whose polygons reference the image"),
)

_LINK_FIELDS = (
    (
        "`osm_type`, `osm_id`, `osm_version`",
        "string, int64, int32",
        "Polygon; join to `polygons` on `osm_type`, `osm_id`",
    ),
    ("`image_id`", "string", "Image; join to `images`"),
    ("`provider`", "string", "Provider of the image"),
    (
        "`source_tag_key`, `source_tag_value`",
        "string",
        "OSM tag the link comes from, as written",
    ),
    ("`canonical_reference`", "string", "Normalized reference that was looked up"),
    ("`asset_index`", "int32", "Position of the image among the reference's results"),
    ("`relation_kind`", "string", "`direct_reference` or `category_membership`"),
    (
        "`source_pbfs`, `observed_osm_versions`",
        "list[string], list[int32]",
        "Extracts and OSM versions where the link was seen",
    ),
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


def _area(value: float) -> str:
    """Format an area in m², hectares, or km², whichever reads best."""
    if value < 10_000:
        return f"{value:,.0f} m²"
    if value < 1_000_000:
        return f"{value / 10_000:,.1f} ha"
    return f"{value / 1_000_000:,.1f} km²"


def _table(header: Sequence[str], rows: Iterable[Sequence[str]], *, numeric: int = 0) -> str:
    """Render a Markdown table whose last ``numeric`` columns are right-aligned."""
    alignment = ["---"] * (len(header) - numeric) + ["---:"] * numeric
    lines = [header, alignment, *rows]
    return "\n".join("| " + " | ".join(line) + " |" for line in lines)


def _size_category(rows: int) -> str:
    bounds = (
        (1_000, "n<1K"),
        (10_000, "1K<n<10K"),
        (100_000, "10K<n<100K"),
        (1_000_000, "100K<n<1M"),
        (10_000_000, "1M<n<10M"),
        (100_000_000, "10M<n<100M"),
    )
    return next((label for bound, label in bounds if rows < bound), "100M<n<1B")


def _frontmatter(statistics: Mapping[str, Any]) -> str:
    assets = statistics["assets"]
    largest = max(int(statistics["rows"]), int(assets["rows"]), int(assets["relationship_rows"]))
    configs = [
        {
            "config_name": name,
            **({"default": True} if name == "polygons" else {}),
            "data_files": [{"split": "train", "path": f"public/{name}.parquet"}],
        }
        for name in ("polygons", "images", "polygon_images")
    ]
    metadata = {
        "license": "odbl",
        "pretty_name": "OSM Polygon Image Tag",
        "tags": [
            "openstreetmap",
            "geospatial",
            "geoparquet",
            "image",
            "wikimedia-commons",
            "street-level-imagery",
        ],
        "size_categories": [_size_category(largest)],
        "configs": configs,
    }
    return yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)


def _summary(statistics: Mapping[str, Any]) -> str:
    assets = statistics["assets"]
    regions = int(statistics["shards"])
    return (
        f"**{_count(assets['direct_urls'])} images with a direct URL, linked to "
        f"{_count(assets.get('polygons_with_direct_url', 0))} of "
        f"{_count(statistics['rows'])} OpenStreetMap polygons in {_count(regions)} "
        f"{'region' if regions == 1 else 'regions'}.**"
    )


def _map_caption(statistics: Mapping[str, Any]) -> str:
    geography = statistics.get("geography") or {}
    resolution = int(geography.get("h3_resolution") or 3)
    cells = int(geography.get("cell_count") or 0)
    peak = int(geography.get("max_cell_count") or 0)
    return (
        f"Polygons per [H3](https://h3geo.org) cell (resolution {resolution}, log scale): "
        f"{_count(cells)} cells, up to {_count(peak)} polygons in one cell. "
        "Each polygon is counted once, at its center."
    )


def _config_table(statistics: Mapping[str, Any]) -> str:
    assets = statistics["assets"]
    return _table(
        ("Config", "One row per", "Rows"),
        (
            ("`polygons` (default)", "OSM polygon", _count(statistics["rows"])),
            ("`images`", "provider image", _count(assets["rows"])),
            ("`polygon_images`", "polygon-image link", _count(assets["relationship_rows"])),
        ),
        numeric=1,
    )


def _provider_table(assets: Mapping[str, Any]) -> str:
    images = assets.get("provider_counts", {})
    image_urls = assets.get("provider_direct_urls", {})
    links = assets.get("link_provider_counts", {})
    usable_links = assets.get("usable_link_provider_counts", {})
    providers = sorted(set(images) | set(links), key=lambda name: (-int(links.get(name, 0)), name))
    rows = [
        (
            name,
            _PROVIDER_TAGS.get(name, ""),
            _count(links.get(name, 0)),
            _count(usable_links.get(name, 0)),
            _count(images.get(name, 0)),
            _count(image_urls.get(name, 0)),
        )
        for name in providers
    ]
    rows.append(
        (
            "**total**",
            "",
            _count(assets["relationship_rows"]),
            _count(assets.get("usable_relationship_rows", 0)),
            _count(assets["rows"]),
            _count(assets["direct_urls"]),
        )
    )
    header = ("Provider", "OSM tags", "Links", "with image URL", "Images", "with image URL")
    return _table(header, rows, numeric=4)


def _relation_table(assets: Mapping[str, Any]) -> str:
    counts = assets.get("image_relation_counts", {})
    usable = int(assets.get("usable_relationship_rows", 0))
    meanings = (
        ("direct_reference", "The tag names this image"),
        ("category_membership", "A file in the Commons category the tag names"),
    )
    rows = (
        (
            f"`{kind}`",
            meaning,
            f"{_count(counts.get(kind, 0))} ({_percentage(counts.get(kind, 0), usable)})",
        )
        for kind, meaning in meanings
    )
    return _table(("`relation_kind`", "Meaning", "Links with image URL"), rows, numeric=1)


def _tag_table(statistics: Mapping[str, Any]) -> str:
    ordered = sorted(
        statistics.get("provider_counts", {}).items(), key=lambda item: (-int(item[1]), item[0])
    )
    return _table(("OSM tag", "Polygons"), ((f"`{t}`", _count(n)) for t, n in ordered), numeric=1)


def _polygon_summary(statistics: Mapping[str, Any]) -> str:
    osm_types = statistics.get("osm_types", {})
    first = str(statistics.get("timestamp_min") or "")[:10]
    last = str(statistics.get("timestamp_max") or "")[:10]
    edits = f" Kept versions were edited between {first} and {last}." if first and last else ""
    return (
        f"{_count(osm_types.get('way', 0))} ways and "
        f"{_count(osm_types.get('relation', 0))} multipolygon relations.{edits}"
    )


def _area_table(statistics: Mapping[str, Any]) -> str:
    quantiles = (statistics.get("area_m2") or {}).get("quantiles") or {}
    labels = {"p10": "10%", "p25": "25%", "p50": "Median", "p75": "75%", "p90": "90%"}
    keys = [key for key in labels if key in quantiles]
    if not keys:
        return ""
    table = _table(
        [labels[key] for key in keys],
        [[_area(float(quantiles[key])) for key in keys]],
        numeric=len(keys),
    )
    return f"Polygon areas:\n\n{table}"


def _status_table(assets: Mapping[str, Any]) -> str:
    ordered = sorted(
        assets.get("status_counts", {}).items(), key=lambda item: (-int(item[1]), item[0])
    )
    rows = (
        (f"`{status}`", _STATUS_MEANINGS.get(status, ""), _count(count))
        for status, count in ordered
    )
    return _table(("`status`", "Meaning", "Images"), rows, numeric=1)


def _methodology(statistics: Mapping[str, Any]) -> str:
    assets = statistics["assets"]
    regions = _count(statistics["shards"])
    return f"""\
1. **Polygons.** Closed ways and multipolygon relations exported with
   [osmium](https://osmcode.org/osmium-tool/) from {regions} regional
   [Geofabrik](https://download.geofabrik.de) extracts, kept when an image tag is non-empty.
   Area is geodesic on WGS 84; polygons without a positive area are dropped.
2. **Deduplication.** Extracts overlap, so one object can appear several times. One row per
   `osm_type` + `osm_id` is kept, newest version first; `source_pbfs` lists every extract.
   {_count(statistics.get("duplicate_observations_removed", 0))} polygon rows,
   {_count(assets.get("duplicate_images_removed", 0))} image rows and
   {_count(assets.get("duplicate_links_removed", 0))} links were removed.
3. **References.** Each tag value is normalized for its provider. `image` values starting with
   `File:`, `Image:` or `Category:` go to Wikimedia Commons; other `image` values must be
   HTTP(S) URLs. Mapillary, Bing Streetside (`bubbleid`) and KartaView need numeric IDs,
   Panoramax a UUID.
4. **Lookup.**
   * Wikimedia Commons: files through the MediaWiki API; a category gives its direct files,
     sorted by page ID, up to 500.
   * `image` URLs: fetched, and kept as an image only if the server answers with an
     `image/*` type.
   * Mapillary (Graph API), Panoramax (metacatalog), KartaView: provider APIs.
     Flickr without an API key and Bing Streetside give a page URL only.
   * URLs that resolve to non-public addresses are refused. Results are cached: this release
     made {_count(assets.get("network_resolutions", 0))} new lookups and reused
     {_count(assets.get("cache_hits", 0))}.
5. **Release.** Images are deduplicated by provider and image identity, links by polygon,
   image and tag. Every number in this card is computed from the published files.

Code: [GitHub]({_CODE_URL}), release [{_RELEASE}]({_CODE_URL}/releases/tag/{_RELEASE}).
Metrics for each release: [Trackio Space]({_TRACKIO_URL})."""


def _fields() -> str:
    return "\n\n".join(
        f"**`{name}`**\n\n{_table(_FIELD_HEADER, rows)}"
        for name, rows in (
            ("polygons", _POLYGON_FIELDS),
            ("images", _IMAGE_FIELDS),
            ("polygon_images", _LINK_FIELDS),
        )
    )


def dataset_card(statistics: dict[str, Any]) -> bytes:
    """Render a card whose numbers all come from the statistics payload."""
    assets = statistics["assets"]
    text = f"""---
{_frontmatter(statistics)}---

![OSM Polygon Image Tag: OpenStreetMap polygons and the images their tags point to]\
({HERO_PNG_RELATIVE})

# OSM Polygon Image Tag

{_summary(statistics)}

Every polygon is a closed way or multipolygon relation that carries an image tag: `image`,
`wikimedia_commons`, `mapillary`, `panoramax`, `kartaview`, `flickr` or `bubbleid`. Each tag
value was looked up at its provider and turned into image rows with a direct URL, size,
license and author when available. There are three tables: `polygons`, `images`, and
`polygon_images` linking the two. Image files are not included, only their URLs.

![Polygons per H3 cell]({GEOGRAPHIC_PNG_RELATIVE})

{_map_caption(statistics)}

## Quick start

```python
from datasets import load_dataset

polygons = load_dataset("{REPO_ID}", "polygons", split="train")
images = load_dataset("{REPO_ID}", "images", split="train")
links = load_dataset("{REPO_ID}", "polygon_images", split="train")
```

Join polygons to their images with DuckDB, reading only what the query needs:

{_QUICK_START_SQL}

## Contents

{_config_table(statistics)}

By provider. A link joins one polygon to one image: an image shared by several polygons is
one row in `images` and one link per polygon.

{_provider_table(assets)}

A Commons `Category:` tag links the polygon to every file directly in that category, not to
one chosen image:

{_relation_table(assets)}

Polygons per OSM tag (a polygon can carry several):

{_tag_table(statistics)}

{_polygon_summary(statistics)}

{_area_table(statistics)}

## Fields

{_fields()}

## Lookup status

Every reference is kept, also when no image was found. `status` says what happened:

{_status_table(assets)}

## Files

```
public/polygons.parquet             polygons (GeoParquet)
public/images.parquet               images
public/polygon_images.parquet       polygon-image links
public/public-manifest.json         row counts and SHA-256 of the public files
statistics/dataset-statistics.json  every number in this card
citation.cff                        citation
```

## Methodology

{_methodology(statistics)}

## License

* OpenStreetMap data (polygons, geometry, tags): [ODbL 1.0]({_ODBL_URL}),
  © OpenStreetMap contributors.
* Images are not part of this dataset and keep their own licenses. `license_id`,
  `license_url` and `author` record what the provider states
  ({_count(assets.get("licensed_assets", 0))} images have a license). A URL is not
  permission to use an image: check the provider page first.
* An image may be outdated, gone, or not show the polygon, above all through a Commons
  category.

## Citation

See [`citation.cff`](citation.cff).
"""
    return text.encode("utf-8")
