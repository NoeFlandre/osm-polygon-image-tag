# Runtime Result Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate the immutable run and verification result contracts from
runtime orchestration without changing APIs or CLI output.

**Architecture:** `runtime/results.py` owns `RunSummary` and `VerifySummary`.
`runtime/orchestrator.py` imports and re-exports them for compatibility while
continuing to own workflow execution, and `cli.py` imports the contracts from
their focused module.

**Tech Stack:** Python 3.12, dataclasses, pytest, Ruff, `ty`, coverage/CRAP,
mutmut, pre-commit, MkDocs, and Just.

---

### Task 1: Establish the ownership contract in red

**Files:**
- Create: `tests/unit/runtime/test_results.py`

- [x] **Step 1: Write the failing test**

```python
import osm_polygon_image_tag.runtime.orchestrator as orchestrator
from osm_polygon_image_tag.runtime.results import RunSummary, VerifySummary


def test_runtime_result_contracts_have_a_focused_owner() -> None:
    assert RunSummary.__module__ == "osm_polygon_image_tag.runtime.results"
    assert VerifySummary.__module__ == "osm_polygon_image_tag.runtime.results"
    assert orchestrator.RunSummary is RunSummary
    assert orchestrator.VerifySummary is VerifySummary
```

- [x] **Step 2: Run the test before creating the module**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/runtime/test_results.py -q --no-cov
```

Expected: collection fails with `ModuleNotFoundError` for
`osm_polygon_image_tag.runtime.results`.

### Task 2: Move the result contracts with compatibility imports

**Files:**
- Create: `src/osm_polygon_image_tag/runtime/results.py`
- Modify: `src/osm_polygon_image_tag/runtime/orchestrator.py`
- Modify: `src/osm_polygon_image_tag/cli.py`
- Test: `tests/unit/runtime/test_results.py`

- [x] **Step 1: Add the unchanged focused result module**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from osm_polygon_image_tag.runtime.enrichment import EnrichmentSummary


@dataclass(frozen=True, slots=True)
class RunSummary:
    processed: int
    built: int
    skipped: int
    accepted_rows: int
    stopped: bool
    enrichment: EnrichmentSummary = field(default_factory=EnrichmentSummary)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VerifySummary:
    checked: int
    valid: int
    invalid: int
    asset_checked: int = 0
    asset_valid: int = 0
    asset_invalid: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

- [x] **Step 2: Preserve the orchestrator import path**

Replace the dataclass definitions and their `asdict`, `dataclass`, and `field`
imports in `orchestrator.py` with:

```python
from osm_polygon_image_tag.runtime.results import RunSummary, VerifySummary
```

Keep all existing references in `run_all` and `verify_all` unchanged.

- [x] **Step 3: Make the CLI dependency explicit**

Import `RunSummary` and `VerifySummary` from
`osm_polygon_image_tag.runtime.results` in `cli.py`; keep `StopToken`,
`graceful_stop_signals`, `run_all`, and `verify_all` imported from
`runtime.orchestrator`.

- [x] **Step 4: Run the focused green checks**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/runtime/test_results.py tests/unit/runtime/test_cli_run.py tests/unit/runtime/test_orchestrator.py -q --no-cov
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check src/osm_polygon_image_tag/runtime/results.py src/osm_polygon_image_tag/runtime/orchestrator.py src/osm_polygon_image_tag/cli.py tests/unit/runtime/test_results.py
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ty check
```

Expected: all selected tests and static checks pass.

### Task 3: Document the boundary after green

**Files:**
- Modify: `src/osm_polygon_image_tag/runtime/README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/development.md`

- [x] **Step 1: Describe ownership**

State that `runtime/results` owns immutable run/verification result contracts,
`runtime/orchestrator` owns workflow coordination, and the old orchestrator
imports remain compatibility aliases.

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

Stage only the new result module, compatibility edits, focused test,
documentation, and this design/plan. Run `git diff --cached --check` and
`git diff-tree --check --no-commit-id -r HEAD`, then commit with:

```bash
git commit -m "refactor: isolate runtime result contracts"
git push origin main
```

Verify a clean worktree and equal local/remote `main` hashes with
`git status --porcelain=v1 -b`, `git log -4 --oneline --decorate`, and
`git ls-remote origin refs/heads/main`.
