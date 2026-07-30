# `artifacts/`

Local persistence, derived reporting, and publication planning for everything
that lives inside the managed data root.

## What belongs here

- Atomic GeoParquet shard writing and validation (`storage`).
- Finalized-manifest discovery (`manifest_inventory`).
- Rebuildable polygon and asset catalogs and factual aggregate queries.
- Deterministic metadata coordination (`reporting`) and card rendering
  (`dataset_card`).
- Publication types, inventory, planning, and receipt writing
  (`publication_types`, `publication_inventory`, `publication`).

## What must not belong here

- Anything that imports the CLI entry point.
- The `osmium` subprocess: that lives in `ingest`.
- Direct imports of `huggingface_hub`; the provider-neutral `Hub` protocol
  belongs here and the concrete SDK adapter belongs in `integrations`.

## Public entry points and contracts

- `write_geoparquet`, `validate_geoparquet`: round-trip-safe shard I/O.
- `verified_manifests`, `verified_asset_manifests`, and their catalog syncs.
- `generate_metadata`: deterministic statistics and card writer.
- `publication_inventory`, `publish_dataset`: the local publication planner
  that takes any `Hub` adapter. Inventory planning is non-destructive:
  temporary or unknown files are preserved and rejected.
- `EXPECTED_REPO`: the constant that callers must echo back via
  `--confirm-repo`.

## Dependencies

- `core` for manifest, schema, errors.
- `pyarrow`, `pyproj`, `shapely`, `sqlite3`.

## Focused tests

```bash
uv run pytest tests/unit/artifacts -q --no-cov
```
