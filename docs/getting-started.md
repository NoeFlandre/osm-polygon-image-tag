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
tree is read-only; all writes go below the selected data root. On the configured
macOS workspace, omitting `--data-root` selects
`/Volumes/Seagate M3/projects/osm-polygon-image-tag` when the volume is
mounted. On other machines, pass `--data-root` or set
`OSM_POLYGON_IMAGE_TAG_DATA_ROOT`; the CLI does not write a fallback directory
inside the repository.

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

## Run the pinned Docker image

The repository also ships a production image with Python 3.12, the locked
Python dependencies, and the external `osmium-tool` executable. Build it from
the repository root:

```bash
docker build --tag osm-polygon-image-tag:0.1.0 .
```

The image has no source data or credentials. Keep the PBF tree on a read-only
bind mount and keep the entire resumable data root on one persistent writable
bind mount. The separate mount points also enforce the pipeline's rule that
source and output roots do not overlap:

```bash
export OSM_SOURCE_ROOT="/path/to/geofabrik-pbf"
export OSM_DATA_ROOT="/path/to/osm-polygon-image-tag"
mkdir -p "$OSM_DATA_ROOT"

docker run --rm --read-only --tmpfs /tmp \
  --mount "type=bind,src=${OSM_SOURCE_ROOT},dst=/raw,readonly" \
  --mount "type=bind,src=${OSM_DATA_ROOT},dst=/data" \
  osm-polygon-image-tag:0.1.0 \
  preflight --source-root /raw --data-root /data --log-format json
```

Start a resumable run with the same mounts. Re-run the same command after a
stop or crash; finalized shards and enrichment checkpoints remain in `/data`:

```bash
docker run --rm --read-only --tmpfs /tmp \
  --mount "type=bind,src=${OSM_SOURCE_ROOT},dst=/raw,readonly" \
  --mount "type=bind,src=${OSM_DATA_ROOT},dst=/data" \
  osm-polygon-image-tag:0.1.0 \
  run --source-root /raw --data-root /data --log-format json
```

Verify before a separate publication, then pass credentials only to the
publishing invocation. The variables below are forwarded from the calling
environment; their values are never written into the image:

```bash
docker run --rm --read-only --tmpfs /tmp \
  --mount "type=bind,src=${OSM_SOURCE_ROOT},dst=/raw,readonly" \
  --mount "type=bind,src=${OSM_DATA_ROOT},dst=/data" \
  osm-polygon-image-tag:0.1.0 \
  verify --source-root /raw --data-root /data --log-format json

docker run --rm --read-only --tmpfs /tmp \
  --mount "type=bind,src=${OSM_SOURCE_ROOT},dst=/raw,readonly" \
  --mount "type=bind,src=${OSM_DATA_ROOT},dst=/data" \
  --env HF_TOKEN --env MAPILLARY_ACCESS_TOKEN --env FLICKR_API_KEY \
  osm-polygon-image-tag:0.1.0 \
  publish --source-root /raw --data-root /data \
  --confirm-repo NoeFlandre/osm-polygon-image-tag --log-format json
```

`HF_TOKEN` is needed only for Hugging Face publication. `MAPILLARY_ACCESS_TOKEN`
and `FLICKR_API_KEY` are optional enrichment credentials; when omitted, those
providers retain factual page-only results. Do not put secrets in a checked-in
`.env` file or bake them into a Docker layer. For an intentional combined run,
replace `run` with `run-and-publish` and pass the same `--confirm-repo` value.

The image uses a direct CLI entrypoint, so Ctrl-C and `SIGTERM` reach the
orchestrator. `--read-only` protects the image filesystem; `/tmp` is a tmpfs for
short-lived resolver and renderer files, while all durable outputs stay under
the mounted data root. If the host data directory is not writable by the
container's selected UID, create it with matching ownership or pass
`--user "$(id -u):$(id -g)"`.

## Production paths used on this machine

These are examples, not defaults:

```text
source: /Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw
data:   /Volumes/Seagate M3/projects/osm-polygon-image-tag
```

Keep the source path read-only and never place PBFs, Parquet files, caches, or
credentials in the Git repository.
