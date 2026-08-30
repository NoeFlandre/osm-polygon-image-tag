# Repository-Wide Mutation Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the mutation-quality gate exercise every covered production module while preserving behavior, resource bounds, equivalent-mutation exclusions, and the existing failure-on-survivor parser.

**Architecture:** Keep mutmut rooted at `src` with `mutate_only_covered_lines = true`, so unreachable code remains outside the campaign. Remove the two selectors that narrow mutation and tests to asset catalog/Flickr; the existing `Justfile` still runs at most two mutation workers and rejects every non-killed result.

**Tech Stack:** Python 3.12, TOML, pytest, mutmut, coverage, Ruff, ty, pre-commit, Just, MkDocs.

---

### Task 1: Lock repository-wide mutation scope with a failing test

**Files:**
- Modify: `tests/unit/core/test_project_foundation.py`
- Read-only context: `pyproject.toml`

- [x] **Step 1: Write the failing scope test**

Add this test after the existing mutation-recipe test. It expresses the desired quality contract and fails against the current two-file scope.

```python
def test_mutation_configuration_covers_all_covered_source() -> None:
    pyproject = loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    mutation = pyproject["tool"]["mutmut"]

    assert mutation["source_paths"] == ["src"]
    assert mutation["mutate_only_covered_lines"] is True
    assert "only_mutate" not in mutation
    assert "pytest_add_cli_args_test_selection" not in mutation
```

- [x] **Step 2: Run the test to verify the red result**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/core/test_project_foundation.py::test_mutation_configuration_covers_all_covered_source -q --no-cov
```

Expected result: one assertion failure at `"only_mutate" not in mutation`, because the current configuration still names two files. This failure is the intended contract gap, not a collection or syntax error.

### Task 2: Remove only the mutation narrowing selectors

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/unit/core/test_project_foundation.py`

- [x] **Step 1: Apply the minimal configuration change**

Delete exactly these existing blocks from `[tool.mutmut]`:

```toml
only_mutate = [
    "src/osm_polygon_image_tag/artifacts/asset_catalog.py",
    "src/osm_polygon_image_tag/resolvers/flickr.py",
]
```

and:

```toml
pytest_add_cli_args_test_selection = [
    "tests/unit/artifacts/test_catalog.py",
    "tests/unit/resolvers/test_flickr.py",
]
```

Leave these settings unchanged:

```toml
source_paths = ["src"]
pytest_add_cli_args = ["--no-cov"]
mutate_only_covered_lines = true
do_not_mutate_patterns = [
    "CREATE TABLE IF NOT EXISTS",
    "CREATE INDEX IF NOT EXISTS",
    "SELECT path, sha256 FROM asset_shards",
    "DELETE FROM asset_",
    "INSERT INTO asset_observations VALUES",
    "INSERT OR REPLACE INTO asset_shards VALUES",
    "cast\\(Any",
    "cast\\(list\\[Mapping",
    "typed_candidates = cast",
    "lambda _event: None",
    "image_url=cast",
]
```

- [x] **Step 2: Run the scope test to verify green**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest tests/unit/core/test_project_foundation.py::test_mutation_configuration_covers_all_covered_source -q --no-cov
```

Expected result: one test passes.

- [x] **Step 3: Run configuration/static checks**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check tests/unit/core/test_project_foundation.py
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff format --check tests/unit/core/test_project_foundation.py
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ty check
```

Expected result: all commands exit zero.

### Task 3: Run the expanded campaign and close survivors

**Files:**
- Modify: `tests/**` only when a surviving mutant identifies an unprotected behavior;
- Modify: `src/**` only when a test demonstrates an actual behavior defect;
- Modify: `pyproject.toml` only for a proven equivalent mutation pattern;
- Modify: `docs/development.md`

- [x] **Step 1: Run the full repository-wide mutation campaign**

Run:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run python scripts/run_mutmut.py run --max-children 2
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run mutmut results --all=true | tee /tmp/osm-polygon-image-tag-mutmut.txt
awk 'NF && $NF != "killed" { print; failed=1 } END { exit failed }' /tmp/osm-polygon-image-tag-mutmut.txt
```

Expected result: the first command completes the expanded covered-source campaign; the result parser prints no non-killed rows and exits zero. If survivors exist, preserve the report and handle each one in Steps 2 or 3 before rerunning this step.

- [x] **Step 2: Add red-green coverage for every real survivor**

For each survivor that changes an observable result, first add one focused test beside the owning module's tests. Run that test with `--no-cov` and confirm it fails against the mutant's behavior. Restore the unmutated source, run the new test to confirm green, then rerun the owning focused test file and the full mutation campaign. Keep the test assertion on public behavior, output, exception, ordering, or persisted state rather than implementation details.

- [x] **Step 3: Exclude only proven equivalent survivors**

No additional exclusions were necessary: the existing patterns cover only
verified equivalent or no-op mutations. The full campaign was rerun after the
survivor tests and the existing exclusions remained unchanged.

- [x] **Step 4: Update developer documentation**

Replace the scoped description in `docs/development.md` with text stating that `just mutation` covers every covered production line under `src`, uses two workers, and retains only the documented equivalent/no-op exclusions. Keep the existing commands and the requirement that no surviving mutant is accepted.

### Task 4: Full validation, review, commit, and push

**Files:**
- Stage only the validated configuration, tests, documentation, runner, plan, and spec files.

- [x] **Step 1: Run the canonical verification gates**

Run:

```bash
MPLBACKEND=Agg MPL_IGNORE_SYSTEM_FONTS=1 MPLCONFIGDIR=/tmp/osm-polygon-image-tag-mplconfig UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just qa
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pre-commit run --all-files
MPLBACKEND=Agg MPL_IGNORE_SYSTEM_FONTS=1 MPLCONFIGDIR=/tmp/osm-polygon-image-tag-mplconfig UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just docs
```

Expected result: all code, test, quality, packaging, hook, and docs gates pass. If packaging smoke cannot resolve PyPI inside the sandbox, rerun the exact unchanged smoke command with network access and retain the environmental explanation.

- [x] **Step 2: Review exact files and mark this plan complete**

Run:

```bash
git diff --check
git status --short --branch
git diff --stat
```

Confirm no unrelated files changed, then mark every completed checklist item in this plan as `[x]`.

- [x] **Step 3: Commit and push**

Run:

```bash
git add -f Justfile docs/development.md docs/superpowers/plans/2026-08-30-repository-mutation-scope.md docs/superpowers/specs/2026-08-30-repository-mutation-scope-design.md pyproject.toml scripts/run_mutmut.py tests/unit/artifacts/test_catalog.py tests/unit/artifacts/test_public_dataset.py tests/unit/artifacts/test_storage.py tests/unit/assets/test_builder.py tests/unit/assets/test_schema.py tests/unit/assets/test_storage.py tests/unit/core/test_atomic.py tests/unit/core/test_paths.py tests/unit/core/test_progress.py tests/unit/core/test_project_foundation.py tests/unit/ingest/test_extraction_stream.py
git commit -m "test: broaden mutation quality coverage"
git push origin main
git status --porcelain=v1 -b
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

Expected result: the commit is pushed, the worktree is clean, and local `HEAD`
equals remote `refs/heads/main`.
