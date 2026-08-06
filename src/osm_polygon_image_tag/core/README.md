# `core/`

Foundational contracts shared by every other layer of the pipeline. Nothing
in this package may import from `ingest`, `artifacts`, `runtime`, or
`integrations`; the dependencies only flow downward.

## What belongs here

- The typed configuration object (`PipelinePaths`) that enforces immutable
  source/output boundaries.
- The error hierarchy (`ImageTagPipelineError`, `ConfigurationError`,
  `PreflightError`, `PublicationError`).
- The PyArrow GeoParquet schema and the manifest shape plus its versioning
  constants.
- The `atomic_write_bytes` primitive used by small durable manifests, metadata,
  receipts, and private caches.
- The progress reporting protocol used by long-running commands.

## What must not belong here

- Pipeline-specific data discovery or validation; callers supply the exact
  paths they own.
- Anything that imports `huggingface_hub` or other provider SDKs.
- Anything that calls `osmium` or other subprocess executables.

## Public entry points and contracts

- `PipelinePaths.build(...)`: rejects overlapping roots, symlinks, and
  non-directory sources.
- `MANIFEST_SCHEMA_VERSION`, `PROCESSING_CONTRACT_VERSION`,
  `DATASET_SCHEMA_VERSION`: stable version integers. Bumping them
  deterministically invalidates older shards.
- `dataset_schema()`: the single source of truth for the GeoParquet schema.
- `Progress`: structural callable contract for emitted events.

## Dependencies

- Only the Python standard library, `pyarrow`, and `pyproj` for the schema.
- No internal package imports.

## Focused tests

```bash
uv run pytest tests/unit/core -q --no-cov
```
