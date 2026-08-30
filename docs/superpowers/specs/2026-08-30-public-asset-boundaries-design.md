# Public-Asset Boundary Refactor Design

## Goal

Make the public-asset implementation easier to understand and test by giving
schema contracts, checkpoint safety, and SQLite deduplication separate homes,
without changing any public API, persisted format, output ordering, or runtime
behavior.

## Current problem

`artifacts/public_assets.py` contains four distinct responsibilities:

1. public image/link schema construction and validation;
2. checkpoint path selection, external-root safety, and storage limits;
3. columnar asset-row preparation and identity/ranking logic; and
4. the resumable SQLite accumulator and Parquet output orchestration.

The code is already covered by strict tests, but the first two responsibilities
are independent contracts embedded in a large implementation module. This
creates hidden coupling: a schema change requires navigating accumulator code,
and a checkpoint-safety change is tested through a module that also owns data
deduplication.

## Chosen approach

Create two focused modules:

- `artifacts/public_asset_schema.py` owns
  `PUBLIC_IMAGE_SCHEMA_VERSION`, `PUBLIC_LINK_SCHEMA_VERSION`, the two schema
  factories, and the two public-table validators.
- `artifacts/public_asset_checkpoint.py` owns checkpoint constants and the
  pure/path-oriented checkpoint helpers: checkpoint-family discovery, external
  root validation, durable/external selection, storage limits, and checkpoint
  metadata/source compatibility checks.

`artifacts/public_assets.py` remains the stable orchestration and deduplication
entry point. It imports the moved names so existing imports such as
`public_assets.public_image_schema` and the current documented public functions
continue to work. `public_dataset.py` imports schema contracts from the focused
schema module directly, making its dependency explicit.

The SQLite accumulator remains in `public_assets.py` because it combines the
asset-specific tables, payload format, ranking rules, and output grouping. Its
compatibility method delegates to the focused checkpoint compatibility helper;
no SQL schema or serialization changes are part of this work.

## Behavior and compatibility contract

- Keep all existing public names and call signatures unchanged.
- Keep the exact Arrow field order, nullability, metadata, and schema versions.
- Keep validator exception types and messages unchanged.
- Keep checkpoint paths, sibling cleanup, symlink rejection, overlap checks,
  storage limits, metadata keys, and compatibility decisions unchanged.
- Keep SQLite tables, pickle protocol, canonical sort keys, row ordering,
  deduplication winners, Parquet compression, and atomic promotion unchanged.
- Do not add a new runtime dependency or alter the package dependency graph
  upward; both new modules remain in `artifacts` and depend only on lower/core
  contracts and PyArrow where already required.

## Testing strategy

The work follows strict red → green → refactor cycles:

1. Add direct tests for the new schema module's exact versions/fields and for
   checkpoint helpers' existing path/compatibility decisions; run them before
   the modules exist and record the expected import failure.
2. Add the focused modules and compatibility imports; run the new tests and
   the existing public-asset/public-dataset tests.
3. Move implementation in small pieces, keeping the focused tests green after
   each move. Existing end-to-end tests remain the regression oracle for output
   bytes, resume behavior, and publication inputs.
4. Run Ruff, ty, the full covered test suite, acceptance and architecture
   tests, CRAP, mutation testing, packaging smoke, pre-commit, strict docs,
   and diff checks before commit/push.

## Non-goals

- redesigning the public dataset format;
- changing the checkpoint storage engine or transaction semantics;
- introducing a generic framework/base class for unrelated accumulators;
- changing performance-sensitive row representations; or
- broad renaming or package-wide churn unrelated to this boundary.
