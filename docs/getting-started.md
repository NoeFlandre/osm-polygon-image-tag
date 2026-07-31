# Getting started

## Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/).
- The `osmium` executable (`brew install osmium-tool` on macOS or
  `apt install osmium-tool` on Debian/Ubuntu).
- A read-only directory containing Geofabrik `.osm.pbf` files.
- A writable data root with enough capacity for polygon and asset shards.
- Hugging Face authentication only when publishing: `hf auth login` or
  `HF_TOKEN`.

Clone and install the locked development environment:

```bash
git clone https://github.com/NoeFlandre/osm-polygon-image-tag.git
cd osm-polygon-image-tag
uv sync --locked --dev
```

## First run

Use a small test tree first, then move to the production paths. The source
tree is read-only; all writes go below `--data-root`.

```bash
uv run osm-polygon-image-tag preflight \
  --source-root "/path/to/geofabrik-pbf" \
  --data-root "/path/to/osm-polygon-image-tag"

uv run osm-polygon-image-tag run-and-publish \
  --source-root "/path/to/geofabrik-pbf" \
  --data-root "/path/to/osm-polygon-image-tag" \
  --confirm-repo NoeFlandre/osm-polygon-image-tag
```

The first command is read-only. The second extracts closed ways and relations,
builds GeoParquet and asset shards, regenerates factual metadata, and publishes
only verified changes. If it stops, run the same command again: completed PBF
and asset shards are reused.

## Production paths used on this machine

These are examples, not defaults:

```text
source: /Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw
data:   /Volumes/Seagate M3/projects/osm-polygon-image-tag
```

Keep the source path read-only and never place PBFs, Parquet files, caches, or
credentials in the Git repository.
