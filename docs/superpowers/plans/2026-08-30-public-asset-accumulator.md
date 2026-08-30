# Public Asset Accumulator Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the bounded public-asset accumulation unit into a focused module while preserving every existing output, checkpoint, import, and resume contract.

**Architecture:** `public_asset_accumulator.py` owns asset batch conversion, deterministic identity/ranking, SQLite persistence, and provenance iteration. `public_assets.py` remains the compatibility facade and owns checkpoint-path selection, source orchestration, Parquet output, and `PublicAssetsResult` assembly.

**Tech Stack:** Python 3.12, SQLite, PyArrow/Parquet, pytest, Ruff, ty, coverage/CRAP, mutmut, pre-commit, Just, MkDocs.

---

### Task 1: Add the ownership contract test

**Files:**
- Create: `tests/unit/artifacts/test_public_asset_accumulator.py`
- Read: `docs/superpowers/specs/2026-08-30-public-asset-accumulator-design.md`

- [ ] **Step 1: Write the failing test**

```python
from osm_polygon_image_tag.artifacts.public_asset_accumulator import _Accumulator


def test_asset_accumulator_is_owned_by_focused_module() -> None:
    assert _Accumulator.__module__ == (
        "osm_polygon_image_tag.artifacts.public_asset_accumulator"
    )
```

- [ ] **Step 2: Run the test to verify it fails for the expected reason**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/artifacts/test_public_asset_accumulator.py -q --no-cov
```

Expected: collection fails with `ModuleNotFoundError` because the focused
module does not exist yet.

### Task 2: Extract the low-level accumulator without changing behavior

**Files:**
- Create: `src/osm_polygon_image_tag/artifacts/public_asset_accumulator.py`
- Modify: `src/osm_polygon_image_tag/artifacts/public_assets.py:55-754`
- Modify: `tests/unit/artifacts/test_public_dataset.py` only if an import path
  must be made explicit; preserve its monkeypatch targets.
- Test: `tests/unit/artifacts/test_public_asset_accumulator.py`

- [ ] **Step 1: Move the complete accumulation block verbatim**

Move these existing definitions, with their signatures, SQL, constants,
serialization protocol, ordering, and transaction behavior unchanged:

```python
_ASSET_DEDUP_COLUMNS
_BatchValues
_AssetBatch
_AssetColumns
_ColumnarAssetRow
_digest
image_identity
_image_identity_values
image_id
_quality_rank
_quality_rank_values
_image_payload
_deduplicate_values
_iter_batches
_prepare_batch_values
_prepare_columnar_batch_values
_append_batch_row
_link_payload
_deduplicate_batch_values
_image_value_wins
_insert_batch_values
_open_asset_connection
_initialize_asset_schema
_initialize_checkpoint_metadata
_Accumulator
```

The new module imports only the dependencies needed by those definitions:
`hashlib`, `json`, `pickle`, `sqlite3`, the existing collection/path/typing
types, `pyarrow` only for the existing batch type inputs, the existing asset
checkpoint constants/helpers, `asset_schema`, and `canonical_json`.

- [ ] **Step 2: Keep the old module as a compatibility facade**

Import the moved definitions into `public_assets.py` so its existing consumers
continue to resolve the same names. The production orchestration continues to
call the imported `_Accumulator` and `_iter_batches` names, which preserves
the existing `monkeypatch.setattr(public_assets_module, "_iter_batches", ...)`
test seam.

```python
from osm_polygon_image_tag.artifacts.public_asset_accumulator import (
    _ASSET_DEDUP_COLUMNS,
    _Accumulator,
    _AssetBatch,
    _AssetColumns,
    _ColumnarAssetRow,
    _digest,
    _iter_batches,
    _prepare_batch_values,
    _prepare_columnar_batch_values,
    image_id,
    image_identity,
)
```

Remove imports used only by the moved block from `public_assets.py`, but keep
checkpoint-policy imports and all output/orchestration dependencies there.

- [ ] **Step 3: Run the focused tests to verify the minimal extraction is green**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/artifacts/test_public_asset_accumulator.py tests/unit/artifacts/test_public_assets.py tests/unit/artifacts/test_public_dataset.py -q --no-cov
```

Expected: all focused tests pass, including checkpoint resume, columnar
conversion, deterministic image/link selection, and existing monkeypatch
coverage.

### Task 3: Refactor only after green and document ownership

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/development.md`
- Modify: `src/osm_polygon_image_tag/artifacts/README.md`
- Modify: `tests/unit/artifacts/test_public_asset_accumulator.py`

- [ ] **Step 1: Clarify the module ownership documentation**

State that `public_asset_accumulator` owns asset transformation, bounded
SQLite accumulation, and provenance iteration, while `public_assets` owns
checkpoint selection, source orchestration, output writing, and result
assembly. Do not add a new abstraction or claim a behavior/performance change.

- [ ] **Step 2: Keep the focused ownership assertion small and deterministic**

The test remains a direct module-ownership assertion; all behavior assertions
stay in the existing real SQLite/Parquet tests. No mock-only assertions or
test-only production methods are introduced.

- [ ] **Step 3: Run static checks and the complexity guard**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff format --check .
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check --select C901 .
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ty check
```

Expected: every command exits 0 with no lint, formatting, complexity, or type
diagnostic.

### Task 4: Run complete verification and publish the exact scope

**Files:**
- Stage only the implementation, tests, ownership documentation, and the two
  committed design/plan files.

- [ ] **Step 1: Run the canonical quality gauntlet**

Run:

```bash
MPLBACKEND=Agg MPL_IGNORE_SYSTEM_FONTS=1 MPLCONFIGDIR=/tmp/osm-polygon-image-tag-mplconfig UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just qa
```

If the package smoke step is blocked by sandbox DNS resolving `hatchling`,
rerun the unchanged smoke command with network access and record that
environmental distinction; do not weaken the gate.

- [ ] **Step 2: Run repository-wide hook and documentation checks**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pre-commit run --all-files
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just docs
```

- [ ] **Step 3: Review whitespace and stage exact paths**

Run `git diff --check` and `git diff --cached --check`, then stage only the
listed implementation, test, and documentation paths. Confirm no generated
site, wheel, coverage, cache, Parquet, or SQLite file is staged.

- [ ] **Step 4: Commit and push after every gate is green**

```bash
git commit -m "refactor: isolate public asset accumulation"
git push origin main
```

Verify `git status --porcelain=v1 -b`, `git log -4 --oneline --decorate`, and
`git ls-remote origin refs/heads/main`; the local and remote hashes must match
and the worktree must be clean.
