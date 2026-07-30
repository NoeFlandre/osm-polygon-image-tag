# `integrations/`

Adapters for external services, isolated from the rest of the pipeline so the
core pipeline never imports a provider SDK directly. Each integration owns its
own protocol plus the concrete adapter that satisfies it.

## What belongs here

- Provider-agnostic protocol definitions that other layers type-hint against.
- Concrete adapters that wrap provider SDKs and translate their errors into
  the project error hierarchy.
- Payload dataclasses that describe the data crossing the protocol boundary.

## What must not belong here

- Imports from `osm_polygon_image_tag.core`, `osm_polygon_image_tag.ingest`,
  `osm_polygon_image_tag.artifacts`, or `osm_polygon_image_tag.runtime`.
  The dependency arrow points the other way.
- Business rules about which files to publish, what the manifest contract is,
  or how the data root is laid out.

## Current integrations

- `huggingface`: `Hub` protocol plus `HuggingFaceHub` adapter plus the
  `PublicationFile` and `HubCommit` payload dataclasses. The adapter wraps
  `huggingface_hub` SDK failures into `PublicationError`.

## Focused tests

```bash
uv run pytest tests/unit/integrations -q --no-cov
```
