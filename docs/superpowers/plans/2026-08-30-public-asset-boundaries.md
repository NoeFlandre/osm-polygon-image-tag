# Public-Asset Boundary Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Separate public-asset schema contracts and checkpoint policy from SQLite deduplication without changing APIs, persisted formats, or outputs.

**Architecture:** Add artifacts/public_asset_schema.py for image/link schemas and validators, and artifacts/public_asset_checkpoint.py for checkpoint paths, safety, limits, and compatibility checks. Keep public_assets.py as the stable deduplication entry point by importing the moved names. Make public_dataset.py depend directly on the schema module.

**Tech Stack:** Python 3.12, PyArrow, SQLite, pytest, Ruff, ty, mutmut, crap4py, MkDocs, uv, Just.

---

### Task 1: Establish red contract tests

**Files:**
- Create: tests/unit/artifacts/test_public_asset_boundaries.py

- [ ] Step 1: Write tests that import the not-yet-existing focused modules.

The test file must contain these assertions:

~~~python
from pathlib import Path

from osm_polygon_image_tag.artifacts.public_asset_checkpoint import (
    _checkpoint_family,
    _checkpoint_limit,
    _checkpoint_root_overlaps,
    _checkpoint_source_is_valid,
)
from osm_polygon_image_tag.artifacts.public_asset_schema import (
    PUBLIC_IMAGE_SCHEMA_VERSION,
    PUBLIC_LINK_SCHEMA_VERSION,
    public_image_schema,
    public_link_schema,
)


def test_public_asset_schema_module_owns_stable_contracts() -> None:
    assert PUBLIC_IMAGE_SCHEMA_VERSION == 1
    assert PUBLIC_LINK_SCHEMA_VERSION == 1
    assert public_image_schema().names[-1] == "source_pbfs"
    assert public_link_schema().names[-1] == "observed_osm_versions"
    assert public_image_schema().metadata == {
        b"osm_polygon_image_tag_public_image_schema_version": b"1"
    }
    assert public_link_schema().metadata == {
        b"osm_polygon_image_tag_public_link_schema_version": b"1"
    }


def test_checkpoint_module_keeps_path_and_limit_decisions(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.sqlite"
    assert _checkpoint_family(checkpoint) == (
        checkpoint,
        Path(f"{checkpoint}-journal"),
        Path(f"{checkpoint}-wal"),
        Path(f"{checkpoint}-shm"),
    )
    assert _checkpoint_root_overlaps(tmp_path / "nested", tmp_path)
    assert not _checkpoint_root_overlaps(tmp_path / "scratch", tmp_path)
    assert _checkpoint_limit(20 * 1024**3, 0) == (20 * 1024**3 - 8 * 1024**3) // 2
    assert _checkpoint_source_is_valid(0, "a", 2, 1, ("a",))
    assert not _checkpoint_source_is_valid(1, "a", 2, 1, ("a",))
    assert not _checkpoint_source_is_valid(0, "a", 2, 3, ("a",))
~~~

- [ ] Step 2: Verify the red state.

Run:
~~~bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/artifacts/test_public_asset_boundaries.py -q --no-cov
~~~

Expected: collection fails with ModuleNotFoundError for one of the two focused modules.

### Task 2: Extract public table schemas and validators

**Files:**
- Create: src/osm_polygon_image_tag/artifacts/public_asset_schema.py
- Modify: src/osm_polygon_image_tag/artifacts/public_assets.py
- Modify: src/osm_polygon_image_tag/artifacts/public_dataset.py
- Test: tests/unit/artifacts/test_public_asset_boundaries.py

- [ ] Step 1: Move the existing public image/link constants, schema factories, and validators unchanged to public_asset_schema.py.

Preserve the exact signatures:
~~~python
def public_image_schema() -> pa.Schema: ...
def public_link_schema() -> pa.Schema: ...
def validate_public_image_parquet(path: Path, *, expected_rows: int | None = None) -> None: ...
def validate_public_link_parquet(path: Path, *, expected_rows: int | None = None) -> None: ...
~~~

Keep the current ValueError messages and Arrow/OSError handling, and export all six names.

- [ ] Step 2: Import the six names into public_assets.py so existing access paths remain valid. Import the validators directly from public_asset_schema.py in public_dataset.py.

- [ ] Step 3: Run the new tests plus tests/unit/artifacts/test_public_assets.py and tests/unit/artifacts/test_public_dataset.py. Expected: all pass.

- [ ] Step 4: Remove the old definitions only after green, format, and rerun the same focused tests.

### Task 3: Extract checkpoint path and safety policy

**Files:**
- Create: src/osm_polygon_image_tag/artifacts/public_asset_checkpoint.py
- Modify: src/osm_polygon_image_tag/artifacts/public_assets.py
- Test: tests/unit/artifacts/test_public_asset_boundaries.py
- Test: tests/unit/artifacts/test_public_assets.py

- [ ] Step 1: Move the current checkpoint constants and these helpers unchanged:
  _checkpoint_family, _legacy_checkpoint_paths, _copy_clean_checkpoint,
  _validate_checkpoint_root, _checkpoint_root_overlaps,
  _can_seed_external_checkpoint, _active_checkpoint,
  _prepare_checkpoint_paths, _checkpoint_max_bytes, _checkpoint_limit,
  _validate_checkpoint_limit, _checkpoint_metadata,
  _checkpoint_metadata_matches, _checkpoint_sources_match, and
  _checkpoint_source_is_valid.

- [ ] Step 2: Add is_compatible_asset_checkpoint(path, input_hashes, polygon_fingerprint) in the new module. It opens the SQLite path, checks metadata and source rows through the moved helpers, returns false for the current OSError/DatabaseError/KeyError/TypeError/ValueError/JSON errors, and closes the connection in finally.

- [ ] Step 3: Import the moved underscore names into public_assets.py to preserve existing focused-test access. Replace the accumulator compatibility body with a thin call to is_compatible_asset_checkpoint.

- [ ] Step 4: Run:
~~~bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/artifacts/test_public_asset_boundaries.py tests/unit/artifacts/test_public_assets.py tests/unit/artifacts/test_public_dataset.py -q --no-cov
~~~
Expected: all checkpoint seeding, symlink, overlap, storage-limit, resume, reuse, and output tests pass.

- [ ] Step 5: Remove moved bodies and unused imports only after green, format, and rerun the focused tests.

### Task 4: Document ownership and inspect

**Files:**
- Modify: src/osm_polygon_image_tag/artifacts/README.md
- Modify: docs/architecture.md
- Modify: docs/development.md

- [ ] Step 1: State the new ownership: public_asset_schema.py owns public Arrow schemas and validators; public_asset_checkpoint.py owns checkpoint safety, selection, limits, and compatibility; public_assets.py owns deduplication and output assembly.

- [ ] Step 2: Run:
~~~bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff format --check .
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ty check
git diff --check
~~~
Expected: all commands pass and only planned files are changed.

### Task 5: Run the complete quality contract

- [ ] Step 1: Run:
~~~bash
MPLBACKEND=Agg MPL_IGNORE_SYSTEM_FONTS=1 MPLCONFIGDIR=/tmp/osm-polygon-image-tag-mplconfig UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just qa
~~~
Expected: locked baseline, lint, type checking, full tests above 90% coverage, acceptance, architecture, CRAP below 6, zero mutation survivors, packaging smoke, and diff review all pass.

- [ ] Step 2: Run:
~~~bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pre-commit run --all-files
MPLBACKEND=Agg MPL_IGNORE_SYSTEM_FONTS=1 MPLCONFIGDIR=/tmp/osm-polygon-image-tag-mplconfig UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just docs
~~~
Expected: all hooks pass and strict documentation builds.

### Task 6: Commit and push

- [ ] Step 1: Verify exact scope:
~~~bash
git status --short --branch
git diff --stat
git diff --check
git diff --cached --check
git diff-tree --check --no-commit-id -r HEAD
~~~

- [ ] Step 2: Commit the plan and each independently green logical move using:
~~~bash
git add -f docs/superpowers/plans/2026-08-30-public-asset-boundaries.md
git add src/osm_polygon_image_tag/artifacts/public_asset_schema.py src/osm_polygon_image_tag/artifacts/public_assets.py src/osm_polygon_image_tag/artifacts/public_dataset.py tests/unit/artifacts/test_public_asset_boundaries.py
git commit -m "refactor: isolate public asset schema contracts"
git add src/osm_polygon_image_tag/artifacts/public_asset_checkpoint.py src/osm_polygon_image_tag/artifacts/public_assets.py tests/unit/artifacts/test_public_assets.py
git commit -m "refactor: isolate public asset checkpoint policy"
git add src/osm_polygon_image_tag/artifacts/README.md docs/architecture.md docs/development.md
git commit -m "docs: describe public asset module boundaries"
~~~

If the plan is committed separately, omit it from later staging and never stage unrelated files.

- [ ] Step 3: Push and verify:
~~~bash
git push origin main
git status --porcelain=v1 -b
git log -4 --oneline --decorate
git ls-remote origin refs/heads/main
~~~
Expected: remote main equals validated local HEAD and the worktree is clean.
