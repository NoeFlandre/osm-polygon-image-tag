# `artifacts/`

Local persistence, derived reporting, and publication planning for everything
that lives inside the managed data root.

## What belongs here

- Atomic GeoParquet shard writing and validation (`storage`).
- The rebuildable catalog index (`catalog`).
- The deterministic statistics JSON and generated dataset card (`reporting`).
- The publication planner, inventory, and receipt writer (`publication`).

## What must not belong here

- Anything that imports the CLI entry point.
- The `osmium` subprocess: that lives in `ingest`.
- Direct imports of `huggingface_hub`: only the `Hub` protocol from
  `integrations.huggingface` is allowed here.

## Public entry points and contracts

- `write_geoparquet`, `validate_geoparquet`: round-trip-safe shard I/O.
- `verified_manifests`, `sync_catalog`: rebuildable catalog with progress
  events.
- `generate_metadata`: deterministic statistics and card writer.
- `publication_inventory`, `publish_dataset`: the local publication planner
  that takes any `Hub` adapter.
- `EXPECTED_REPO`: the constant that callers must echo back via
  `--confirm-repo`.

## Dependencies

- `core` for manifest, schema, errors.
- `integrations.huggingface` for the `Hub` protocol and the
  `PublicationFile` / `HubCommit` payload dataclasses.
- `pyarrow`, `pyproj`, `shapely`, `sqlite3`.

## Focused tests

```bash
uv run pytest tests/unit/artifacts -q --no-cov
```
