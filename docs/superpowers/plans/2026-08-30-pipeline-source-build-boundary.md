# Runtime Source-Build Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract per-PBF source materialization from `build_one` into a focused module while preserving every existing runtime behavior and compatibility seam.

**Architecture:** `runtime/pipeline.py` remains the public facade for artifact paths, resume/deep verification, identity/manifest assembly, and `build_one`/`verify_one`. New `runtime/pipeline_build.py` owns only the bounded TagStore scan, export restoration, row transformation, GeoParquet write, and existing `RunCounts` collection. The extraction uses existing types and callables; it does not add a new runtime abstraction.

**Tech Stack:** Python 3.12, PyArrow/GeoParquet, pytest, Ruff, ty, coverage, crap4py, mutmut, pre-commit, Just.

---

### Task 1: Write and verify the ownership test

**Files:**
- Create: `tests/unit/runtime/test_pipeline_build.py`
- Read-only context: `src/osm_polygon_image_tag/runtime/pipeline.py`

- [x] **Step 1: Write the failing test**

Create the focused ownership test below. It specifies that the new source-build entry point is defined by its focused module and is re-exported privately by the existing pipeline facade for the extraction boundary.

```python
import osm_polygon_image_tag.runtime.pipeline as pipeline
from osm_polygon_image_tag.runtime.pipeline_build import build_source_output


def test_source_building_is_owned_by_focused_module() -> None:
    assert build_source_output.__module__ == (
        "osm_polygon_image_tag.runtime.pipeline_build"
    )
    assert pipeline._build_source_output is build_source_output
```

- [x] **Step 2: Run the test to verify the red result**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/runtime/test_pipeline_build.py -q --no-cov
```

Expected result: collection fails with `ModuleNotFoundError` because `runtime.pipeline_build` does not exist yet. This confirms the test is testing the intended missing ownership boundary rather than existing behavior.

### Task 2: Extract the source-build implementation

**Files:**
- Create: `src/osm_polygon_image_tag/runtime/pipeline_build.py`
- Modify: `src/osm_polygon_image_tag/runtime/pipeline.py`
- Test: `tests/unit/runtime/test_pipeline_build.py`

- [x] **Step 1: Add the focused source-build module**

Create `src/osm_polygon_image_tag/runtime/pipeline_build.py` with this implementation. It keeps the old call order, tag batch threshold, exporter configuration, transformation behavior, output writer, and count semantics.

```python
"""Build one source shard after the runtime pipeline selects it."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from osm_polygon_image_tag.artifacts.storage import WriteResult, write_geoparquet
from osm_polygon_image_tag.core.manifest import RunCounts
from osm_polygon_image_tag.ingest.discovery import PbfSource
from osm_polygon_image_tag.ingest.extraction import (
    ExportRecord,
    SourceTagRecord,
    restore_original_tags,
)
from osm_polygon_image_tag.ingest.tag_store import TagStore
from osm_polygon_image_tag.ingest.transform import AcceptedRow, RejectedRow, transform_records
from osm_polygon_image_tag.runtime.resources import osmium_export_config

Scanner = Callable[..., None]
Exporter = Callable[..., Iterable[ExportRecord]]
_TAG_STORE_BATCH_SIZE = 1000


def build_source_output(
    source: PbfSource,
    *,
    data_root: Path,
    output_path: Path,
    scanner: Scanner,
    exporter: Exporter,
    executable: str,
    batch_size: int,
) -> tuple[WriteResult, RunCounts]:
    """Scan, restore, transform, and write one source shard."""
    with TagStore.create(data_root) as tags:
        _scan_source_tags(source.absolute_path, scanner, tags)
        records = restore_original_tags(
            exporter(
                source.absolute_path,
                osmium_export_config(),
                executable=executable,
            ),
            lookup=tags.lookup,
            lookup_many=tags.lookup_many,
        )
        return _write_transformed_output(
            records,
            source_pbf=source.relative_path.as_posix(),
            output_path=output_path,
            batch_size=batch_size,
        )


def _scan_source_tags(
    source_path: Path,
    scanner: Scanner,
    tags: TagStore,
) -> None:
    pending_tags: list[SourceTagRecord] = []

    def emit_tag(record: SourceTagRecord) -> None:
        pending_tags.append(record)
        if len(pending_tags) == _TAG_STORE_BATCH_SIZE:
            tags.add_many(pending_tags)
            pending_tags.clear()

    scanner(source_path, emit=emit_tag)
    if pending_tags:
        tags.add_many(pending_tags)
    tags.flush()


def _write_transformed_output(
    records: Iterable[ExportRecord],
    *,
    source_pbf: str,
    output_path: Path,
    batch_size: int,
) -> tuple[WriteResult, RunCounts]:
    accepted_rows = 0
    rejections: Counter[str] = Counter()

    def rows() -> Iterator[dict[str, object]]:
        nonlocal accepted_rows
        for outcome in transform_records(records, source_pbf=source_pbf):
            if isinstance(outcome, RejectedRow):
                rejections[outcome.reason] += 1
                continue
            assert isinstance(outcome, AcceptedRow)
            accepted_rows += 1
            yield outcome.values

    write_result = write_geoparquet(rows(), output_path, batch_size=batch_size)
    return write_result, RunCounts(
        accepted_rows=accepted_rows,
        rejections=dict(sorted(rejections.items())),
    )
```

- [x] **Step 2: Wire `build_one` to the focused module**

In `src/osm_polygon_image_tag/runtime/pipeline.py`:

1. Keep `TagStore` imported as a compatibility seam for existing tests/callers that patch `pipeline.TagStore.create`; the new module imports the same class object.
2. Remove the old `Counter`, `Iterator`, `SourceTagRecord`, `restore_original_tags`, `AcceptedRow`, `RejectedRow`, and `transform_records` imports that are no longer used by the facade.
3. Add this import:

```python
from osm_polygon_image_tag.runtime.pipeline_build import (
    build_source_output as _build_source_output,
)
```

4. In `build_one`, keep the existing artifact-path calculation, reusable fast path, and `source_identity` call. Replace the `TagStore.create` block, nested callbacks, and local counters with:

```python
write_result, counts = _build_source_output(
    source,
    data_root=paths.data_root,
    output_path=output_path,
    scanner=scanner,
    exporter=exporter,
    executable=executable,
    batch_size=batch_size,
)
```

5. Keep output hashing, `OutputIdentity`, `Manifest`, `write_manifest`, and `BuildResult` construction unchanged except that `counts.accepted_rows` and `counts.rejections` come from the returned `RunCounts`.

- [x] **Step 3: Run the focused green checks**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check src/osm_polygon_image_tag/runtime/pipeline.py src/osm_polygon_image_tag/runtime/pipeline_build.py tests/unit/runtime/test_pipeline_build.py
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff format --check src/osm_polygon_image_tag/runtime/pipeline.py src/osm_polygon_image_tag/runtime/pipeline_build.py tests/unit/runtime/test_pipeline_build.py
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ty check
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/runtime/test_pipeline_build.py tests/unit/runtime/test_pipeline.py -q --no-cov
```

Expected result: the ownership test and all existing runtime pipeline tests pass, Ruff/format report no issues, and ty reports `All checks passed!`.

### Task 3: Refactor review and documentation

**Files:**
- Modify: `src/osm_polygon_image_tag/runtime/README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/development.md`
- Modify: `docs/superpowers/plans/2026-08-30-pipeline-source-build-boundary.md`

- [x] **Step 1: Document the ownership boundary**

Update the runtime documentation so it states that `pipeline` owns artifact paths, resume/deep verification, identity/manifest assembly, and public entry points, while `pipeline_build` owns bounded source scan, export restoration, transformation, and GeoParquet writing. State that no artifact naming or manifest behavior belongs in `pipeline_build`.

- [x] **Step 2: Run the full verification gates**

Run the repository's canonical commands with isolated writable caches:

```bash
MPLBACKEND=Agg MPL_IGNORE_SYSTEM_FONTS=1 MPLCONFIGDIR=/tmp/osm-polygon-image-tag-mplconfig UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just qa
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pre-commit run --all-files
MPLBACKEND=Agg MPL_IGNORE_SYSTEM_FONTS=1 MPLCONFIGDIR=/tmp/osm-polygon-image-tag-mplconfig UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just docs
```

Expected result: all configured gates pass. If `just qa` stops at `just smoke` because sandbox DNS cannot resolve PyPI, rerun the exact unchanged smoke command with network access and record the environmental cause; do not weaken the gate.

- [x] **Step 3: Review the diff and update this plan**

Run:

```bash
git diff --check
git diff --stat
git status --short --branch
```

Confirm only the planned runtime module, focused test, documentation, and plan/spec files changed. Mark every completed checklist item in this plan with `[x]`.

### Task 4: Commit and publish

**Files:**
- Stage only the exact files reviewed in Task 3.

- [x] **Step 1: Stage and inspect the exact patch**

Run:

```bash
git add -f docs/architecture.md docs/development.md docs/superpowers/plans/2026-08-30-pipeline-source-build-boundary.md docs/superpowers/specs/2026-08-30-pipeline-source-build-boundary-design.md src/osm_polygon_image_tag/runtime/README.md src/osm_polygon_image_tag/runtime/pipeline.py src/osm_polygon_image_tag/runtime/pipeline_build.py tests/unit/runtime/test_pipeline_build.py
just diff-review
git diff --cached --stat
git diff --cached --name-only
git diff --cached --check
```

Expected result: staged paths are exactly the listed files and all diff checks exit successfully.

- [x] **Step 2: Commit with the focused Conventional Commit message**

Run:

```bash
git commit -m "refactor: isolate runtime source building"
```

Expected result: one new commit is created with no unstaged changes.

- [x] **Step 3: Push and verify synchronization**

Run:

```bash
git push origin main
git status --porcelain=v1 -b
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

Expected result: push succeeds, the worktree is clean, and the local commit SHA equals the remote `refs/heads/main` SHA.
