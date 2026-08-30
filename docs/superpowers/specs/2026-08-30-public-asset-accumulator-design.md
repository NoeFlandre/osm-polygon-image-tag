# Public Asset Accumulator Boundary

## Context

`artifacts/public_assets.py` currently combines four responsibilities: public
asset identity and payload preparation, bounded SQLite accumulation, public
Parquet writing, and checkpoint/source orchestration. The file is the largest
production artifact module after the recent polygon boundary extraction, and
its private accumulation implementation is difficult to test independently
from release assembly.

## Goals

- Give bounded asset transformation and SQLite persistence one focused owner.
- Keep release output writing, checkpoint-path policy, and source orchestration
  in `public_assets.py`.
- Preserve all existing image/link rows, ordering, identifiers, checkpoint
  tables, resume semantics, error behavior, and public imports.
- Preserve the allocation-light columnar ingestion path and its bounded-memory
  behavior exactly.

## Non-goals

- No schema, checkpoint format, SQL, batching, ranking, or algorithm changes.
- No new public API and no broad abstraction shared with polygon accumulation.
- No changes to resolver behavior, asset shard creation, or publication.

## Design

Create `artifacts/public_asset_accumulator.py` as the owner of the complete
low-level asset materialization unit:

- `_BatchValues`, `_AssetBatch`, `_AssetColumns`, and `_ColumnarAssetRow`;
- image/link identity, payload, ranking, batch preparation, deduplication, and
  SQLite insertion helpers; and
- `_Accumulator`, including its SQLite schema, checkpoint transactions,
  source counts, grouped provenance reads, and output row iterators.

The new module will preserve the existing signatures and implementations. It
will depend downward on `public_asset_checkpoint`, `assets.schema`, and core
serialization only. `public_assets.py` will retain `PublicAssetsResult`,
Parquet writers, checkpoint selection, source processing, result assembly, and
`build_public_asset_tables`.

`public_assets.py` remains a compatibility facade by importing the moved
public helpers (`image_id`, `image_identity`) and the private names currently
used by the repository's tests and downstream internal callers
(`_Accumulator`, `_ASSET_DEDUP_COLUMNS`, `_AssetBatch`, `_AssetColumns`,
`_ColumnarAssetRow`, `_digest`, `_iter_batches`,
`_prepare_batch_values`, and `_prepare_columnar_batch_values`). This keeps
existing import paths working while making direct ownership explicit in a new
focused test.

## Data flow

```text
asset Parquet batch
        |
        v
public_asset_accumulator: columnar view -> payload/rank/link values
        |
        v
public_asset_accumulator: bounded SQLite checkpoint accumulator
        |
        v
public_assets: deterministic image/link Parquet writers
        |
        v
PublicAssetsResult
```

The accumulator's transaction boundaries, SQLite pragmas, table definitions,
serialization protocol, ordering queries, and source/provenance grouping are
unchanged. Only Python module ownership changes.

## Testing and quality gates

The first test will import the new module directly and assert that
`_Accumulator` is defined there; it must fail with
`ModuleNotFoundError` before implementation. After the minimal extraction,
the new ownership test and existing public-dataset/public-assets tests must
pass. Existing monkeypatches of `public_assets._iter_batches` and all
compatibility imports remain in place. Ruff complexity, Ruff formatting, ty,
the full test suite, integration/architecture tests, CRAP, mutation testing,
the wheel smoke test, pre-commit, and strict documentation build are required
before publication.
