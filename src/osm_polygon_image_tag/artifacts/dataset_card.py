"""Render the deterministic Hugging Face dataset card."""

from typing import Any


def dataset_card(statistics: dict[str, Any]) -> bytes:
    """Render a card containing only facts derived from the statistics payload."""
    providers = "\n".join(
        f"- `{provider}`: {count}" for provider, count in statistics["provider_counts"].items()
    )
    text = f"""---
license: odbl
tags:
- openstreetmap
- geospatial
- geoparquet
- image
---
# OSM Polygon Image Tag

This dataset contains OpenStreetMap Polygon and MultiPolygon observations whose
way or relation carries at least one raw image-reference tag.

## Current verified contents

- Shards: {statistics["shards"]}
- Rows: {statistics["rows"]}
- Duplicate observations across source PBFs: {statistics["duplicate_observations"]}

Provider observations:
{providers}

## Schema

Rows include OSM type/ID/version/changeset/timestamp, source PBF identity, full
OGC:CRS84 WKB geometry, geodesic `area_m2`, bounds, every original OSM tag, and
the exact raw `image`, `wikimedia_commons`, `mapillary`, `panoramax`,
`panoramax_values`, `kartaview`, `flickr`, and `bubbleid` values.

## Provenance and license

Source extracts are provided by Geofabrik from OpenStreetMap. OpenStreetMap data
is available under the Open Database License. Attribution: © OpenStreetMap
contributors.

## Limitations and intended use

References may be stale, inaccessible, provider-specific, or unrelated to the
current feature. Inclusion does not establish image copyright, licensing,
safety, availability, or correspondence to the mapped feature. No provider API
is called and no image is downloaded or validated.

Overlapping Geofabrik extracts are intentionally preserved as separate
observations and quantified above. Statistics in this card are generated only
from finalized manifests and their size-checked GeoParquet shards.
"""
    return text.encode("utf-8")
