# Enrichment Contract Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate immutable enrichment contracts from the concurrent worker without changing APIs, behavior, or CLI output.

**Architecture:** `runtime/enrichment_types.py` owns `AssetJob` and
`EnrichmentSummary`. `runtime/enrichment.py` continues to own
`EnrichmentWorker` and re-exports the moved types for compatibility;
`runtime/orchestrator.py` and `runtime/results.py` import the focused types
module directly.

**Tech Stack:** Python 3.12, dataclasses, pytest, Ruff, `ty`, coverage/CRAP,
mutmut, pre-commit, MkDocs, and Just.

---

### Task 1: Establish the ownership contract in red

**Files:**
- Create: `tests/unit/runtime/test_enrichment_types.py`

- [x] **Step 1: Write the failing test**

```python
import osm_polygon_image_tag.runtime.enrichment as enrichment
from osm_polygon_image_tag.runtime.enrichment_types import AssetJob, EnrichmentSummary


def test_enrichment_contracts_have_a_focused_owner() -> None:
    assert AssetJob.__module__ == "osm_polygon_image_tag.runtime.enrichment_types"
    assert EnrichmentSummary.__module__ == "osm_polygon_image_tag.runtime.enrichment_types"
    assert enrichment.AssetJob is AssetJob
    assert enrichment.EnrichmentSummary is EnrichmentSummary
```

- [x] **Step 2: Run the test before creating the module**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/runtime/test_enrichment_types.py -q --no-cov
```

Expected: collection fails with `ModuleNotFoundError` for
`osm_polygon_image_tag.runtime.enrichment_types`.

### Task 2: Move the contracts with compatibility imports

**Files:**
- Create: `src/osm_polygon_image_tag/runtime/enrichment_types.py`
- Modify: `src/osm_polygon_image_tag/runtime/enrichment.py`
- Modify: `src/osm_polygon_image_tag/runtime/orchestrator.py`
- Modify: `src/osm_polygon_image_tag/runtime/results.py`
- Test: `tests/unit/runtime/test_enrichment_types.py`

- [x] **Step 1: Add the unchanged focused contract module**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from osm_polygon_image_tag.core.manifest import Manifest


@dataclass(frozen=True, slots=True)
class AssetJob:
    manifest: Manifest
    polygon_path: Path


@dataclass(frozen=True, slots=True)
class EnrichmentSummary:
    built: int = 0
    skipped: int = 0
    pending: int = 0
    rows: int = 0
    statuses: dict[str, int] | None = None

    def status_counts(self) -> dict[str, int]:
        return dict(self.statuses or {})
```

The final module must not add unused imports; the exact implementation keeps
only the `dataclass` and `Path` dependencies required by the contracts.

- [x] **Step 2: Preserve the worker module's import path**

Remove the two dataclass definitions from `enrichment.py` and replace them
with:

```python
from osm_polygon_image_tag.runtime.enrichment_types import AssetJob, EnrichmentSummary
```

Keep `_asset_artifacts_present`, `EnrichmentWorker`, and all worker state and
methods unchanged.

- [x] **Step 3: Make direct consumers explicit**

Import `AssetJob` and `EnrichmentSummary` from
`osm_polygon_image_tag.runtime.enrichment_types` in `orchestrator.py`, and
import `EnrichmentSummary` from the same module in `results.py`. Keep all
workflow references and result fields unchanged.

- [x] **Step 4: Run the focused green checks**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/runtime/test_enrichment_types.py tests/unit/runtime/test_enrichment.py tests/unit/runtime/test_orchestrator.py tests/unit/runtime/test_results.py -q --no-cov
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check src/osm_polygon_image_tag/runtime/enrichment_types.py src/osm_polygon_image_tag/runtime/enrichment.py src/osm_polygon_image_tag/runtime/orchestrator.py src/osm_polygon_image_tag/runtime/results.py tests/unit/runtime/test_enrichment_types.py
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ty check
```

Expected: all selected tests and static checks pass.

### Task 3: Document the boundary after green

**Files:**
- Modify: `src/osm_polygon_image_tag/runtime/README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/development.md`

- [x] **Step 1: Describe ownership**

State that `runtime/enrichment_types` owns immutable enrichment contracts,
`runtime/enrichment` owns worker lifecycle and concurrency, and the worker
module retains compatibility imports for existing callers.

- [x] **Step 2: Run documentation and complexity checks**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff format --check .
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check --select C901 .
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ty check
```

Expected: all commands exit 0.

### Task 4: Run the complete quality contract and publish

- [x] **Step 1: Run the canonical gate**

```bash
MPLBACKEND=Agg MPL_IGNORE_SYSTEM_FONTS=1 MPLCONFIGDIR=/tmp/osm-polygon-image-tag-mplconfig UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just qa
```

If only packaging smoke fails on sandbox DNS, rerun the exact unchanged
`just smoke` command with network access and record the environmental cause.

- [x] **Step 2: Run repository hooks and strict docs**

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pre-commit run --all-files
MPLBACKEND=Agg MPL_IGNORE_SYSTEM_FONTS=1 MPLCONFIGDIR=/tmp/osm-polygon-image-tag-mplconfig UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just docs
```

- [x] **Step 3: Review, commit, push, and verify**

Stage only the new enrichment contract module, compatibility edits, focused
test, documentation, and this design/plan. Run `git diff --cached --check`
and `git diff-tree --check --no-commit-id -r HEAD`, then commit with:

```bash
git commit -m "refactor: isolate enrichment contracts"
git push origin main
```

Verify a clean worktree and equal local/remote `main` hashes with
`git status --porcelain=v1 -b`, `git log -4 --oneline --decorate`, and
`git ls-remote origin refs/heads/main`.
