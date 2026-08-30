# `artifacts/`

Local persistence, derived reporting, and publication planning for everything
that lives inside the managed data root.

## What belongs here

- Atomic GeoParquet shard writing and validation (`storage`).
- Finalized-manifest discovery (`manifest_inventory`).
- Rebuildable polygon and asset catalogs and factual aggregate queries.
- Public image/link Arrow schemas and Parquet validators (`public_asset_schema`).
- Public-asset checkpoint selection, compatibility, and storage-safety policy
  (`public_asset_checkpoint`).
- SQLite-backed polygon selection, provenance, and checkpoint persistence
  (`public_polygon_accumulator`).
- Deterministic metadata coordination (`reporting`) and card rendering
  (`dataset_card`).
- A deterministic deduplicated release view (`public_dataset`) that keeps
  per-PBF inputs private for resume while publishing canonical polygons,
  unique images, and the `polygon_images` link table under `public/`.
  Its polygon pass checkpoints completed source PBFs in the private
  `tmp/.public-polygons.sqlite` database and resumes unfinished sources.
  The image/link pass uses the private `tmp/.public-assets.sqlite` checkpoint;
  it commits each asset shard separately and resumes unfinished shards. Both
  checkpoints are removed only after their public outputs and manifest are
  written successfully.
- The public asset row module (`public_asset_rows`) owns deterministic
  columnar/mapping transformation and bounded-batch deduplication. The public
  asset accumulator (`public_asset_accumulator`) owns SQLite persistence and
  provenance iteration. The public asset materializer (`public_assets`) owns
  checkpoint selection, source orchestration, and output assembly; its schema
  and checkpoint dependencies remain focused in the modules above.
- The public dataset validation boundary (`public_dataset_validation`) owns
  release schemas, manifest/digest/row-count validation, and reuse checks.
  The public dataset materializer (`public_dataset`) owns polygon persistence,
  source orchestration, reuse, and cleanup. Public result construction and
  manifest/output serialization live in `public_dataset_output`; the
  materializer retains compatibility imports for existing callers.
- Publication types, inventory, planning, and receipt writing
  (`publication_types`, `publication_inventory`, `publication`).
- The dataset-card geographic density map (`geography/`): typed models,
  H3 helpers, validated Parquet input pruning, the bundled Natural Earth
  basemap, the deterministic matplotlib renderer, and the per-shard
  cached aggregation pipeline.

## What must not belong here

- Anything that imports the CLI entry point.
- The `osmium` subprocess: that lives in `ingest`.
- Direct imports of `huggingface_hub`; the provider-neutral `Hub` protocol
  belongs here and the concrete SDK adapter belongs in `integrations`.

## Public entry points and contracts

- `write_geoparquet`, `validate_geoparquet`: round-trip-safe shard I/O.
- `verified_manifests`, `verified_asset_manifests`, and their catalog syncs.
- `generate_metadata`: deterministic statistics and card writer; also
  refreshes the H3 geographic density map.
- `publication_inventory`, `publish_dataset`: the local publication planner
  that takes any `Hub` adapter. Inventory planning is non-destructive:
  temporary or unknown files are preserved and rejected. The geographic
  PNG is required, validated, and verified at receive time.
- `EXPECTED_REPO`: the constant that callers must echo back via
  `--confirm-repo`.

## Dependencies

- `core` for manifest, schema, errors.
- `pyarrow`, `pyproj`, `shapely`, `sqlite3`, `h3`, `matplotlib`.

## Focused tests

```bash
uv run pytest tests/unit/artifacts -q --no-cov
```
