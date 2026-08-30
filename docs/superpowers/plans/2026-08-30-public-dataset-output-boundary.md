# Public Dataset Output Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate public dataset result and manifest output assembly from polygon materialization without changing APIs, artifacts, or output bytes.

**Architecture:** `artifacts/public_dataset_output.py` owns the immutable
`PublicDatasetResult` contract and final manifest write. `artifacts/public_dataset.py`
continues to own polygon checkpointing, source processing, reuse, and cleanup,
while importing the output names for compatibility.

**Tech Stack:** Python 3.12, dataclasses, PyArrow, pytest, Ruff, `ty`,
coverage/CRAP, mutmut, pre-commit, MkDocs, and Just.

---

### Task 1: Establish the output ownership contract in red

**Files:**
- Create: `tests/unit/artifacts/test_public_dataset_output.py`

- [x] **Step 1: Write the failing test**

```python
import osm_polygon_image_tag.artifacts.public_dataset as public_dataset
from osm_polygon_image_tag.artifacts.public_dataset_output import (
    PublicDatasetResult,
    _manifest_payload,
    _write_public_dataset,
)


def test_public_dataset_output_has_a_focused_owner() -> None:
    assert PublicDatasetResult.__module__ == (
        "osm_polygon_image_tag.artifacts.public_dataset_output"
    )
    assert _manifest_payload.__module__ == (
        "osm_polygon_image_tag.artifacts.public_dataset_output"
    )
    assert _write_public_dataset.__module__ == (
        "osm_polygon_image_tag.artifacts.public_dataset_output"
    )
    assert public_dataset.PublicDatasetResult is PublicDatasetResult
    assert public_dataset._manifest_payload is _manifest_payload
    assert public_dataset._write_public_dataset is _write_public_dataset
```

- [x] **Step 2: Run the test before creating the module**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/artifacts/test_public_dataset_output.py -q --no-cov
```

Expected: collection fails with `ModuleNotFoundError` for
`osm_polygon_image_tag.artifacts.public_dataset_output`.

### Task 2: Move output assembly with compatibility imports

**Files:**
- Create: `src/osm_polygon_image_tag/artifacts/public_dataset_output.py`
- Modify: `src/osm_polygon_image_tag/artifacts/public_dataset.py`
- Test: `tests/unit/artifacts/test_public_dataset_output.py`

- [x] **Step 1: Move the existing output contract and functions unchanged**

Create the output module with the following implementation, retaining the
existing field order, default, manifest keys, and positional result
construction:

```python
"""Assemble and persist the deterministic public dataset outputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from osm_polygon_image_tag.artifacts.public_assets import PublicAssetsResult
from osm_polygon_image_tag.artifacts.public_dataset_validation import (
    PUBLIC_IMAGE_RELATIVE,
    PUBLIC_LINK_RELATIVE,
    PUBLIC_MANIFEST_RELATIVE,
    PUBLIC_POLYGON_RELATIVE,
    PUBLIC_SCHEMA_VERSION,
)
from osm_polygon_image_tag.core.atomic import atomic_write_bytes
from osm_polygon_image_tag.core.manifest import Manifest, file_sha256
from osm_polygon_image_tag.core.serialization import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class PublicDatasetResult:
    """Publishable deduplicated artifacts and their data-derived counts."""

    polygon_path: Path
    image_path: Path
    link_path: Path
    manifest_path: Path
    polygon_manifest: Manifest
    polygon_rows: int
    image_rows: int
    link_rows: int
    duplicate_polygon_rows: int
    duplicate_image_rows: int
    duplicate_link_rows: int
    orphan_asset_rows: int
    reused: bool = False


def _manifest_payload(
    polygon_manifests: Sequence[tuple[Any, Path]],
    asset_manifests: Sequence[tuple[Any, Path]],
    polygon_manifest: Manifest,
    assets: PublicAssetsResult,
    *,
    polygon_rows: int,
    image_rows: int,
    link_rows: int,
    duplicate_polygon_rows: int,
    duplicate_image_rows: int,
    duplicate_link_rows: int,
    orphan_asset_rows: int,
) -> dict[str, Any]:
    def output(path: Path, rows: int) -> dict[str, object]:
        return {
            "sha256": file_sha256(path),
            "size_bytes": path.stat().st_size,
            "row_count": rows,
        }

    return {
        "public_schema_version": PUBLIC_SCHEMA_VERSION,
        "polygon_inputs": [m.output.sha256 for m, _ in polygon_manifests],
        "asset_inputs": [m.output.sha256 for m, _ in asset_manifests],
        "polygon_output": {
            "sha256": polygon_manifest.output.sha256,
            "size_bytes": polygon_manifest.output.size_bytes,
            "row_count": polygon_rows,
        },
        "image_output": output(assets.image_path, image_rows),
        "link_output": output(assets.link_path, link_rows),
        "polygon_rows": polygon_rows,
        "image_rows": image_rows,
        "link_rows": link_rows,
        "duplicate_polygon_rows": duplicate_polygon_rows,
        "duplicate_image_rows": duplicate_image_rows,
        "duplicate_link_rows": duplicate_link_rows,
        "orphan_asset_rows": orphan_asset_rows,
    }


def _write_public_dataset(
    root: Path,
    polygon_manifests: Sequence[tuple[Any, Path]],
    source_assets: Sequence[tuple[Any, Path]],
    polygon_manifest: Manifest,
    assets: PublicAssetsResult,
    *,
    polygon_rows: int,
    input_polygon_rows: int,
) -> PublicDatasetResult:
    payload = _manifest_payload(
        polygon_manifests,
        source_assets,
        polygon_manifest,
        assets,
        polygon_rows=polygon_rows,
        image_rows=assets.image_rows,
        link_rows=assets.link_rows,
        duplicate_polygon_rows=input_polygon_rows - polygon_rows,
        duplicate_image_rows=assets.duplicate_image_rows,
        duplicate_link_rows=assets.duplicate_link_rows,
        orphan_asset_rows=assets.orphan_rows,
    )
    atomic_write_bytes(
        root / PUBLIC_MANIFEST_RELATIVE,
        canonical_json_bytes(payload, newline=True),
        prefix=".public-manifest.",
        suffix=".tmp",
        sync_directory=True,
    )
    return PublicDatasetResult(
        root / PUBLIC_POLYGON_RELATIVE,
        assets.image_path,
        assets.link_path,
        root / PUBLIC_MANIFEST_RELATIVE,
        polygon_manifest,
        polygon_rows,
        assets.image_rows,
        assets.link_rows,
        input_polygon_rows - polygon_rows,
        assets.duplicate_image_rows,
        assets.duplicate_link_rows,
        assets.orphan_rows,
    )
```

- [x] **Step 2: Preserve the existing public-dataset module paths**

Remove the moved dataclass and functions and their now-unused imports from
`public_dataset.py`, then add:

```python
from osm_polygon_image_tag.artifacts.public_dataset_output import (
    PublicDatasetResult,
    _manifest_payload,  # noqa: F401 - compatibility import
    _write_public_dataset,  # noqa: F401 - compatibility import
)
```

Keep `build_public_dataset`, `_try_reuse`, `_reuse_result`, and every polygon
processing helper unchanged.

- [x] **Step 3: Run the focused green checks**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/artifacts/test_public_dataset_output.py tests/unit/artifacts/test_public_dataset.py tests/unit/artifacts/test_public_dataset_validation.py -q --no-cov
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check src/osm_polygon_image_tag/artifacts/public_dataset_output.py src/osm_polygon_image_tag/artifacts/public_dataset.py tests/unit/artifacts/test_public_dataset_output.py
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff format --check src/osm_polygon_image_tag/artifacts/public_dataset_output.py src/osm_polygon_image_tag/artifacts/public_dataset.py tests/unit/artifacts/test_public_dataset_output.py
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ty check
```

Expected: all selected tests and static checks pass.

### Task 3: Document the output boundary after green

**Files:**
- Modify: `src/osm_polygon_image_tag/artifacts/README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/development.md`

- [x] **Step 1: Describe ownership**

State that `public_dataset_output` owns the immutable result contract and
manifest/output writing, while `public_dataset` owns polygon materialization,
reuse, source orchestration, and cleanup; note the compatibility imports.

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

Stage only the output-boundary module, compatibility edits, focused test,
documentation, and this design/plan. Run `git diff --cached --check` and
`git diff-tree --check --no-commit-id -r HEAD`, then commit with:

```bash
git commit -m "refactor: isolate public dataset output"
git push origin main
```

Verify a clean worktree and equal local/remote `main` hashes with
`git status --porcelain=v1 -b`, `git log -4 --oneline --decorate`, and
`git ls-remote origin refs/heads/main`.
