# Public Asset Row Boundary Design

## Goal

Make the public-asset accumulation code easier to understand and test by
separating row shaping from SQLite persistence without changing behavior.

## Current problem

`artifacts/public_asset_accumulator.py` is already isolated from release
orchestration, but it still contains two independent concerns. Its first
roughly half turns mapping or columnar Parquet rows into deterministic image
and link values, while its second half owns SQLite schema, checkpoint
transactions, and provenance reads. The shared module-level namespace makes a
row-contract change harder to review and test independently from persistence.

## Chosen design

Create `artifacts/public_asset_rows.py` as the owner of:

- the asset batch and columnar-row contracts;
- stable image identity, public IDs, payloads, and quality ranking;
- bounded batch iteration and preparation; and
- in-batch deduplication and SQLite-ready value construction.

Keep `artifacts/public_asset_accumulator.py` responsible for SQLite insertion,
schema initialization, checkpoint transactions, counts, and grouped
provenance iteration. It imports the row helpers and continues to re-export
their existing private names. `public_assets.py` continues importing through
the accumulator facade, so current tests and internal callers keep their
existing access paths.

## Compatibility contract

- Keep all existing function and class signatures unchanged.
- Keep image/link IDs, payload fields, pickle protocol, canonical sort keys,
  deduplication winners, ordering, and Arrow output unchanged.
- Keep `_Accumulator` and all current compatibility imports available from
  `public_asset_accumulator` and `public_assets`.
- Do not change SQLite schema, pragmas, checkpoint metadata, transaction
  boundaries, or source/provenance behavior.

## Testing strategy

Start with a direct module-ownership test that imports the new module before
it exists and confirms the intended red state. After the smallest extraction,
run the ownership test plus the existing public asset and public dataset tests.
Only after green, update ownership documentation and run the complete lint,
type, coverage, mutation, packaging, pre-commit, and docs gates.
