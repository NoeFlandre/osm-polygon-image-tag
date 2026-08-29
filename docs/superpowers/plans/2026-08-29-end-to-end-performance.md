# End-to-End Performance Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove measured serialization and SQLite call overhead from PBF ingestion and asset enrichment while preserving every public contract and output.

**Architecture:** Keep scalar APIs and pipeline ordering. Add one bounded batch boundary around source-tag persistence, and replace generic recursive resolution-record serialization with an explicit equivalent payload representation. Do not change durability, resolver concurrency, schemas, cache keys, or geodesic algorithms.

**Tech Stack:** Python 3.12, SQLite, PyArrow/GeoParquet, asyncio, pytest, Ruff, ty, cProfile, mutmut, CRAP.

---

### Task 1: Replace recursive resolution serialization

**Files:**
- Modify: `src/osm_polygon_image_tag/assets/resolution.py`
- Test: `tests/unit/assets/test_cache.py`

- [ ] **Step 1: Add the behavior-preserving contract test.**

Add a test that constructs a `ResolutionRecord` with a nested asset mapping,
calls `record_payload`, and asserts the exact prior keys, tuple shape, ISO
timestamp conversion, and deep detachment:

```python
def test_record_payload_preserves_shape_and_detaches_asset_values() -> None:
    record = ResolutionRecord(
        "panoramax",
        "reference",
        1,
        "resolved",
        ({"metadata": {"width": 1024}},),
        None,
    )

    payload = record_payload(record)

    assert payload == {
        "provider": "panoramax",
        "canonical_reference": "reference",
        "resolver_contract_version": 1,
        "status": "resolved",
        "assets": ({"metadata": {"width": 1024}},),
        "retry_after": None,
        "reason": None,
        "category_truncated": False,
        "attempt_count": 1,
    }
    assert payload["assets"] is not record.assets
    assert payload["assets"][0] is not record.assets[0]
    assert payload["assets"][0]["metadata"] is not record.assets[0]["metadata"]
```

- [ ] **Step 2: Add the red performance guard.**

Add a second test that monkeypatches `osm_polygon_image_tag.assets.resolution.dataclasses.asdict` to raise `AssertionError`, then calls `record_payload`. Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest -q tests/unit/assets/test_cache.py::test_record_payload_does_not_use_recursive_dataclass_traversal --no-cov
```

Expected: the guard fails against the current implementation because
`record_payload` still calls `asdict`. The contract test may already pass at
this point; its purpose is to lock the existing behavior while the guard
proves the implementation change is real.

- [ ] **Step 3: Implement the minimal explicit equivalent.**

Import `copy`, remove the unused `asdict` import, and make
`record_payload` return the same nine keys. Copy each asset with
`copy.deepcopy`, keep `assets` as a tuple, and convert only
`retry_after` to ISO text. Do not alter `canonical_record_bytes` or any
cache schema.

- [ ] **Step 4: Run focused green checks.**

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest -q tests/unit/assets/test_cache.py --no-cov
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check src/osm_polygon_image_tag/assets/resolution.py tests/unit/assets/test_cache.py
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ty check
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit the green serialization change.**

```bash
git add src/osm_polygon_image_tag/assets/resolution.py tests/unit/assets/test_cache.py
git commit -m "perf: avoid recursive resolution serialization"
```

### Task 2: Batch source-tag persistence without changing single-add behavior

**Files:**
- Modify: `src/osm_polygon_image_tag/ingest/tag_store.py`
- Modify: `src/osm_polygon_image_tag/runtime/pipeline.py`
- Test: `tests/unit/ingest/test_tag_store.py`
- Test: `tests/unit/runtime/test_pipeline.py`

- [ ] **Step 1: Add a red `TagStore.add_many` test.**

Add a test that passes two `SourceTagRecord` values to `add_many`, then
asserts `lookup_many` and `count` return both exact tag mappings. Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest -q tests/unit/ingest/test_tag_store.py::test_store_add_many_round_trips_all_tags_and_commits_at_batch_boundary --no-cov
```

Expected: `AttributeError` because the method is absent.

- [ ] **Step 2: Implement bounded `add_many`.**

Define one shared `INSERT INTO tags` SQL constant and a private helper that
returns `(osm_type, osm_id, canonical_json(tags))`. Keep `add` as its
existing immediate `execute` path and preserve its exact duplicate error.
Implement `add_many(records)` by materializing only the supplied bounded
batch, calling `executemany`, translating an integrity failure to a
`ValueError`, incrementing `_pending` by the batch length, and calling
`flush` when the existing `commit_interval` is reached. Preserve
`synchronous=FULL`, table schema, and transaction boundaries.

- [ ] **Step 3: Add a red pipeline batching test.**

Use the existing pipeline test helpers with a fake `TagStore.create` context
whose `add_many` records lengths and whose `add` raises if called. Have the
scanner emit 2,500 source records and assert batch lengths
`[1000, 1000, 500]`, ordered export, and the existing `BuildResult`.
Run the focused pipeline tests and confirm this assertion fails before wiring.

- [ ] **Step 4: Wire the existing commit-sized batches.**

In `build_one`, collect scanner emissions in a list capped at 1,000 records,
call `tags.add_many` at the cap, and submit the final partial list after the
scanner returns. Keep the scanner callback signature and all downstream
restore/transform behavior unchanged.

- [ ] **Step 5: Run focused green checks and commit.**

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest -q tests/unit/ingest/test_tag_store.py tests/unit/runtime/test_pipeline.py --no-cov
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check src/osm_polygon_image_tag/ingest/tag_store.py src/osm_polygon_image_tag/runtime/pipeline.py tests/unit/ingest/test_tag_store.py tests/unit/runtime/test_pipeline.py
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ty check
git add src/osm_polygon_image_tag/ingest/tag_store.py src/osm_polygon_image_tag/runtime/pipeline.py tests/unit/ingest/test_tag_store.py tests/unit/runtime/test_pipeline.py
git commit -m "perf: batch PBF tag-store inserts"
```

Expected: all checks pass and only the planned four files are in the commit.

### Task 3: Verify performance, resources, and complete compatibility

**Files:** no additional source files unless a failing regression requires a
focused correction.

- [ ] **Step 1: Run the same six-run PBF benchmark before and after.**

Use `/tmp/osm-polygon-image-tag-10k.osm.pbf`, three alternating scalar-control
and optimized builds, fresh temporary data roots, and `build_one`. Assert
10,000 accepted rows, empty rejection maps, and byte-identical output Parquet
on every run. Record median wall time and peak RSS.

- [ ] **Step 2: Run the no-network asset benchmark and cProfile.**

Use a 10,000-row synthetic polygon shard and deterministic registry. Compare
full asset-shard wall time and resolution payload CPU time, and verify the
post-change profile no longer shows recursive dataclass traversal as a top
cost.

- [ ] **Step 3: Run the complete quality gate.**

```bash
MPLBACKEND=Agg MPL_IGNORE_SYSTEM_FONTS=1 MPLCONFIGDIR=/tmp/osm-polygon-image-tag-mplconfig UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just qa
```

If only sandbox DNS prevents `hatchling` resolution, rerun the exact
`just smoke` command with approved network access. Do not waive code,
coverage, acceptance, architecture, CRAP, mutation, packaging, or diff
failures.

- [ ] **Step 4: Review scope and push.**

```bash
git diff --check
git status --short --branch
git log --oneline --decorate -3
git push origin main
git status --porcelain=v1 -b
git diff-tree --check --no-commit-id -r HEAD
```

Expected: all planned commits are on the current branch, the working tree is
clean, and `main` is aligned with `origin/main`.
