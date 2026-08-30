# Public Dataset Validation Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move public-release validation and manifest reuse helpers into a focused module while preserving the `public_dataset` compatibility surface and every existing output.

**Architecture:** `public_dataset_validation.py` will own the deterministic release schema, artifact validation, manifest parsing, and reuse metadata helpers. `public_dataset.py` will retain polygon materialization, source orchestration, public manifest assembly, cleanup, and the existing entry points; it will import moved helpers as compatibility aliases. No persisted contract or algorithm changes are allowed.

**Tech Stack:** Python 3.12, PyArrow, pytest, uv, Ruff, ty, coverage, crap4py, mutmut, MkDocs.

---

### Task 1: Add the ownership contract

**Files:**
- Create: `tests/unit/artifacts/test_public_dataset_validation.py`

- [ ] **Step 1: Write the failing test**

```python
from osm_polygon_image_tag.artifacts.public_dataset_validation import public_polygon_schema


def test_public_dataset_validation_owns_release_schema() -> None:
    assert public_polygon_schema.__module__ == (
        "osm_polygon_image_tag.artifacts.public_dataset_validation"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/artifacts/test_public_dataset_validation.py -q --no-cov
```

Expected: collection fails with `ModuleNotFoundError` for `public_dataset_validation`, proving the test checks the new boundary rather than existing behavior.

### Task 2: Extract validation and reuse helpers

**Files:**
- Create: `src/osm_polygon_image_tag/artifacts/public_dataset_validation.py`
- Modify: `src/osm_polygon_image_tag/artifacts/public_dataset.py`

- [ ] **Step 1: Move the existing implementations without changing them**

Move these definitions to `public_dataset_validation.py`: `public_polygon_schema`, `_validate_public_polygon`, `_validate_public_polygon_schema`, `_validate_public_polygon_rows`, `_public_polygon_schema_matches`, `validate_public_dataset`, `_read_public_manifest`, `_public_output_paths`, `_validate_public_output`, `_validate_public_parquet_files`, `_public_polygon_manifest`, `_manifest_polygon_row_count`, `_manifest_polygon_output_matches`, and `_nonnegative_row_count`.

Retain their current signatures and bodies. The new module must import `json`, `Mapping`, `Path`, `Any`, `pyarrow`, `pyarrow.parquet`, `validate_public_image_parquet`, `validate_public_link_parquet`, `Manifest`, `OutputIdentity`, `RunCounts`, `SourceIdentity`, `DATASET_SCHEMA_VERSION`, `MANIFEST_SCHEMA_VERSION`, `PROCESSING_CONTRACT_VERSION`, `dataset_schema`, and `file_sha256`. Define `PUBLIC_SCHEMA_VERSION = 2`, `PUBLIC_POLYGON_RELATIVE = "public/polygons.parquet"`, `PUBLIC_IMAGE_RELATIVE = "public/images.parquet"`, `PUBLIC_LINK_RELATIVE = "public/polygon_images.parquet"`, and `PUBLIC_MANIFEST_RELATIVE = "public/public-manifest.json"` in the new module.

Import those names into `public_dataset.py` and keep its existing public path constants as aliases to the same values. Keep direct compatibility imports for private helpers used by existing tests.

- [ ] **Step 2: Run the focused ownership and regression tests**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/artifacts/test_public_dataset_validation.py tests/unit/artifacts/test_public_dataset.py -q --no-cov
```

Expected: all focused tests pass, including existing reuse, schema, resume, cleanup, and output assertions.

### Task 3: Refactor documentation and import hygiene

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/development.md`
- Modify: `src/osm_polygon_image_tag/artifacts/README.md`

- [ ] **Step 1: Document ownership**

State that `public_dataset_validation` owns public schema, manifest, digest, row-count, and reuse validation, while `public_dataset` owns materialization and orchestration. Keep the existing `public_polygon_accumulator` and public-asset module responsibilities unchanged.

- [ ] **Step 2: Run focused static checks**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff format --check .
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ty check
```

Expected: all commands exit 0.

### Task 4: Run the complete quality gates

**Files:**
- No additional source files.

- [ ] **Step 1: Run the canonical repository gate**

Run:

```bash
MPLBACKEND=Agg MPL_IGNORE_SYSTEM_FONTS=1 MPLCONFIGDIR=/tmp/osm-polygon-image-tag-mplconfig UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just qa
```

Expected: baseline, Ruff, ty, full tests, acceptance, architecture, CRAP below 6, mutation results with only `killed`, package smoke, and diff review all pass. If package smoke cannot resolve PyPI inside the sandbox, rerun the exact unchanged `just smoke` command with network escalation and record the environmental distinction.

- [ ] **Step 2: Run hooks and strict docs**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pre-commit run --all-files
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just docs
```

Expected: every hook and the strict MkDocs build exit 0.

### Task 5: Commit, push, and verify

**Files:**
- Stage only the design, plan, source, tests, and documentation files listed above.

- [ ] **Step 1: Review and commit the exact scope**

Run:

```bash
git diff --check
git diff --cached --check
git status --short
git commit -m "refactor: isolate public dataset validation"
```

Expected: no whitespace errors, only the planned files are staged, and the commit completes without hooks failing.

- [ ] **Step 2: Push and verify synchronization**

Run:

```bash
git push origin main
git status --porcelain=v1 -b
git log -5 --oneline --decorate
git diff-tree --check --no-commit-id -r HEAD
git ls-remote origin refs/heads/main
```

Expected: push succeeds, worktree is clean, local `HEAD` and `origin/main` share the new commit, and the remote ref hash matches the local commit hash.
