# `integrations/`

Adapters for external services, isolated from the rest of the pipeline so the
core pipeline never imports a provider SDK directly. Provider-neutral protocols
and payloads live in `artifacts`; each integration owns only its concrete
adapter.

## What belongs here

- Concrete adapters that wrap provider SDKs and translate their errors into
  the project error hierarchy.
- Payload dataclasses that describe the data crossing the protocol boundary.

## What must not belong here

- Imports from `osm_polygon_image_tag.ingest` or
  `osm_polygon_image_tag.runtime`.
- Business rules about which files to publish, what the manifest contract is,
  or how the data root is laid out.

## Current integrations

- `huggingface`: `HuggingFaceHub`, implementing the artifact-layer `Hub`
  protocol and translating `huggingface_hub` SDK failures into
  `PublicationError`.
- `trackio`: optional publisher for the numeric snapshot in
  `statistics/dataset-statistics.json`. It is deliberately not part of the
  normal pipeline's required dependencies or network path.

## Publishing the metrics Space

After generating and publishing the dataset, install Trackio only for this
one operation and log the current deterministic statistics snapshot:

```bash
uv run --with trackio python -m osm_polygon_image_tag.integrations.trackio \
  "/Volumes/Seagate M3/projects/osm-polygon-image-tag"
```

The command creates or updates
[`NoeFlandre/osm-polygon-image-tag-trackio`](https://huggingface.co/spaces/NoeFlandre/osm-polygon-image-tag-trackio).
The Space receives row, shard, provider, resolver-status, direct-image, retry,
and geographic-cell metrics. The statistics file's SHA-256 digest is stored in
the Trackio run configuration so each run can be tied to an exact snapshot.
An authenticated `hf auth login` session with permission to create or update
the Space is required; no token is written to the repository or dataset.

## Focused tests

```bash
uv run pytest tests/unit/integrations -q --no-cov
```
