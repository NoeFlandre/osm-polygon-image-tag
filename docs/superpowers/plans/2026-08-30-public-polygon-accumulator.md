# Public Polygon Accumulator Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate the SQLite-backed public polygon accumulator and simplify the deterministic checkpoint race test without changing persisted data, public outputs, or APIs.

**Architecture:** Add `artifacts/public_polygon_accumulator.py` for polygon identity, ranking, SQLite persistence, source provenance, and checkpoint compatibility. Keep `public_dataset.py` as the public schema, manifest, validation, reuse, and orchestration entry point, with compatibility imports for existing module-level consumers. Extract only fixture setup and synchronization branches from the race test into test helpers.

**Tech Stack:** Python 3.12, PyArrow, SQLite, pytest, Ruff, ty, coverage, crap4py, mutmut, MkDocs, uv, Just.

---

### Task 1: Establish the new module contract in red

**Files:**
- Create: `tests/unit/artifacts/test_public_polygon_accumulator.py`
- Modify: `tests/unit/artifacts/test_public_dataset.py`

- [ ] **Step 1: Add direct focused-module tests before creating the production module.**

Create `tests/unit/artifacts/test_public_polygon_accumulator.py` with:

```python
from osm_polygon_image_tag.artifacts.public_polygon_accumulator import (
    _PolygonAccumulator,
    _advance_polygon_source_group,
)


def test_polygon_accumulator_is_owned_by_focused_module() -> None:
    assert _PolygonAccumulator.__module__ == (
        "osm_polygon_image_tag.artifacts.public_polygon_accumulator"
    )
    groups = iter([("way", 1, "a"), ("way", 2, "b")])
    assert _advance_polygon_source_group(next(groups), groups, ("way", 1)) == (
        "way",
        1,
        "a",
    )
```

Import `_PolygonAccumulator`, `_advance_polygon_source_group`, and
`_remove_incompatible_polygon_checkpoint` from the same not-yet-created module
in `test_public_dataset.py`, removing those names from its `public_dataset`
import block. This makes the existing real SQLite behavior tests exercise the
new owner directly.

- [ ] **Step 2: Verify the red state.**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/artifacts/test_public_polygon_accumulator.py tests/unit/artifacts/test_public_dataset.py -q --no-cov
```

Expected result: collection fails with `ModuleNotFoundError` for
`osm_polygon_image_tag.artifacts.public_polygon_accumulator`.

### Task 2: Extract the polygon accumulator unchanged

**Files:**
- Create: `src/osm_polygon_image_tag/artifacts/public_polygon_accumulator.py`
- Modify: `src/osm_polygon_image_tag/artifacts/public_dataset.py`
- Test: `tests/unit/artifacts/test_public_polygon_accumulator.py`
- Test: `tests/unit/artifacts/test_public_dataset.py`

- [ ] **Step 1: Move the accumulator implementation after the red check.**

Move `_identity`, `_polygon_rank`, `_stable_row_key`, `_PolygonAccumulator`,
`_remove_incompatible_polygon_checkpoint`, `_initialize_polygon_checkpoint`,
`_polygon_source_groups`, `_advance_polygon_source_group`,
`_polygon_row_with_sources`, `_source_pbf_values`,
`_polygon_checkpoint_metadata`, `_polygon_checkpoint_metadata_matches`,
`_polygon_checkpoint_sources_match`, `_completed_sources_match`, and
`_valid_polygon_checkpoint_source` without changing their SQL, pickle protocol,
rank tuple, source order, transactions, exceptions, or signatures.

The new module owns `PUBLIC_DEDUP_CHECKPOINT_SCHEMA_VERSION = 1` and imports
only `json`, `pickle`, `sqlite3`, the collection protocols, `Path`, `Any`,
`remove_checkpoint_files`, and `canonical_json`.

- [ ] **Step 2: Preserve the existing dataset module access paths.**

Replace the removed definitions in `public_dataset.py` with:

```python
from osm_polygon_image_tag.artifacts.public_polygon_accumulator import (
    PUBLIC_DEDUP_CHECKPOINT_SCHEMA_VERSION,
    _PolygonAccumulator,
    _advance_polygon_source_group,  # noqa: F401 - compatibility import
    _remove_incompatible_polygon_checkpoint,  # noqa: F401 - compatibility import
)
```

Remove only imports no longer used there, retaining manifest/release imports
and the existing public constants and function signatures.

- [ ] **Step 3: Run focused behavior and static checks.**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/artifacts/test_public_polygon_accumulator.py tests/unit/artifacts/test_public_dataset.py -q --no-cov
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check src/osm_polygon_image_tag/artifacts/public_polygon_accumulator.py src/osm_polygon_image_tag/artifacts/public_dataset.py tests/unit/artifacts/test_public_polygon_accumulator.py tests/unit/artifacts/test_public_dataset.py
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ty check
```

Expected result: all selected tests and both static checks pass.

### Task 3: Decompose the over-complex deterministic race test

**Files:**
- Modify: `tests/unit/runtime/test_orchestrator.py`

- [ ] **Step 1: Preserve the observed C901 contract.**

The clean baseline reports `test_checkpoint_publication_race_with_active_pbf_build`
at complexity 12 while the configured limit is 10. Keep its assertions,
five-second waits, `threading.Event`/`threading.Barrier` protocol, and
`publication_inventory` observation unchanged; do not suppress C901.

- [ ] **Step 2: Extract fixture construction.**

Move the race test's local imports to the module import block. Add the
test-only `_CheckpointRaceFixture` dataclass and
`_checkpoint_race_fixture(tmp_path)` helper for the existing paths, worker,
callbacks, events, and synchronization primitives. The helper creates the
same minimal manifest and returns the same `build`, `metadata`, and `publish`
callbacks.

- [ ] **Step 3: Extract both synchronization outcomes.**

Add `_assert_unserialized_publication_race(fixture, callback_thread)` and
`_assert_serialized_publication(fixture, callback_thread)` helpers. Each keeps
the current temporary-file assertions, event releases, callback join, and
`race_observed` assertions. The top-level test should only coordinate setup,
thread startup, branch selection, final thread assertions, and cleanup.

- [ ] **Step 4: Verify the race behavior and complexity gate.**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/runtime/test_orchestrator.py::test_checkpoint_publication_race_with_active_pbf_build -q --no-cov
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check --select C901 tests/unit/runtime/test_orchestrator.py
```

Expected result: the race test passes and Ruff reports no C901 findings.

### Task 4: Document, verify, commit, and push

**Files:**
- Modify: `src/osm_polygon_image_tag/artifacts/README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/development.md`

- [ ] **Step 1: Document the ownership boundary.**

State that `public_polygon_accumulator` owns SQLite polygon selection,
provenance, and checkpoint persistence; `public_dataset` owns public schemas,
manifest validation, reuse, and orchestration; and the race test uses focused
deterministic helpers.

- [ ] **Step 2: Run the complete quality contract.**

Run `ruff check .`, `ruff format --check .`, `ty check`,
`ruff check --select C901 .`, `git diff --check`, the canonical
`MPLBACKEND=Agg MPL_IGNORE_SYSTEM_FONTS=1 MPLCONFIGDIR=/tmp/osm-polygon-image-tag-mplconfig UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just qa`, `uv run pre-commit run --all-files`, and strict `just docs`. If packaging smoke alone is blocked by sandbox DNS, rerun the unchanged `just smoke` with network access and record the environmental distinction.

- [ ] **Step 3: Commit only the validated scope.**

Stage the new accumulator module, its tests, the race-test refactor, and the
three ownership documentation files. Commit with:

```bash
git commit -m "refactor: isolate polygon accumulation boundary"
```

- [ ] **Step 4: Push and verify synchronization.**

Run `git push origin main`, then `git status --porcelain=v1 -b`,
`git log -4 --oneline --decorate`, and `git ls-remote origin refs/heads/main`.
The final state must have a clean worktree and matching local/remote `main`.
