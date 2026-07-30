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

## Focused tests

```bash
uv run pytest tests/unit/integrations -q --no-cov
```
