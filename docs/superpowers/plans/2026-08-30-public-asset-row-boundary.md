# Public Asset Row Boundary Implementation Plan

**Goal:** Isolate public-asset row transformation and bounded deduplication
from SQLite persistence while preserving every output, checkpoint, and import
contract.

## Red -> green -> refactor

1. Add a direct ownership test for `_AssetBatch` and `image_identity` in the
   new `public_asset_rows` module. Run it before creating the module and record
   the expected import failure.
2. Move the row contracts and helpers unchanged into `public_asset_rows.py`.
   Import them into `public_asset_accumulator.py` so `_Accumulator` continues
   to call the same names and old callers still resolve them.
3. Run the focused row, asset, and dataset tests. Then update architecture and
   development documentation only after the behavior is green.
4. Run Ruff, `ty`, the full test/acceptance/architecture suite, CRAP, the
   configured mutation scope, packaging smoke, pre-commit, and strict docs.
5. Review the exact diff, commit the logical extraction, push `main`, and
   verify local and remote hashes match with a clean worktree.

## Files

- Create: `src/osm_polygon_image_tag/artifacts/public_asset_rows.py`
- Modify: `src/osm_polygon_image_tag/artifacts/public_asset_accumulator.py`
- Modify: `tests/unit/artifacts/test_public_asset_accumulator.py`
- Modify: `docs/architecture.md`, `docs/development.md`, and the artifacts
  README ownership list.

## Non-goals

No algorithm, serialization, schema, SQL, concurrency, checkpoint, API, or
performance behavior changes; no generic abstraction shared with the polygon
accumulator.
