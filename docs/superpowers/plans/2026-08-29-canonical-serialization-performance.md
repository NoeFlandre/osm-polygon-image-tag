# Canonical Serialization Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove redundant deep-copy and UTF-8 conversion work from internal asset-resolution canonicalization while preserving every externally visible representation.

**Architecture:** Keep `record_payload()` as the detached caller-facing representation. Add a shared canonical payload builder with copying disabled for read-only hashing, use it from canonical bytes and resolution snapshots, and hash canonical bytes directly in cache writes.

**Tech Stack:** Python 3.12, SQLite, canonical JSON, pytest, Ruff, ty, PyArrow/GeoParquet, cProfile.

---

### Task 1: Separate detached and canonical resolution payload paths

**Files:**
- Modify: `src/osm_polygon_image_tag/assets/resolution.py`
- Modify: `src/osm_polygon_image_tag/assets/cache.py`
- Test: `tests/unit/assets/test_cache.py`

- [ ] **Step 1: Write failing tests for canonical byte equivalence and no-copy use.**

Extend `tests/unit/assets/test_cache.py` with:

```python
from osm_polygon_image_tag.core.serialization import canonical_json_bytes
from osm_polygon_image_tag.assets.resolution import canonical_record_bytes


def test_canonical_record_bytes_match_detached_payload() -> None:
    record = _record()
    assert canonical_record_bytes(record) == canonical_json_bytes(record_payload(record))


def test_canonical_paths_do_not_deepcopy_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _record()
    with ResolutionCache.open(tmp_path) as cache:
        cache.put(record)
        expected = cache.resolution_snapshot((record.key,), records={record.key: record})
        expected_bytes = canonical_json_bytes(record_payload(record))

        def unexpected_deepcopy(_value: object) -> object:
            raise AssertionError("canonical serialization copied assets")

        monkeypatch.setattr(resolution, "deepcopy", unexpected_deepcopy)

        assert cache.resolution_snapshot((record.key,), records={record.key: record}) == expected
        assert canonical_record_bytes(record) == expected_bytes
```

Use the existing `_record` helper or create the record inline if the test
module has no fixture. Run the focused test before implementation:

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest -q tests/unit/assets/test_cache.py::test_canonical_paths_do_not_deepcopy_assets --no-cov
```

Expected: FAIL because the current canonical and snapshot paths call the
detached `record_payload` implementation.

- [ ] **Step 2: Implement the shared payload helper.**

In `resolution.py`, keep `record_payload(record)` returning the detached
payload, add a canonical payload function that reuses the same field mapping
with asset copying disabled, and make `canonical_record_bytes` serialize that
canonical payload. The shape is:

```python
def record_payload(record: ResolutionRecord) -> dict[str, object]:
    return _record_payload(record, copy_assets=True)


def canonical_record_payload(record: ResolutionRecord) -> dict[str, object]:
    return _record_payload(record, copy_assets=False)


def _record_payload(
    record: ResolutionRecord, *, copy_assets: bool
) -> dict[str, object]:
    assets = (
        tuple(deepcopy(asset) for asset in record.assets)
        if copy_assets
        else record.assets
    )
    return {
        "provider": record.provider,
        "canonical_reference": record.canonical_reference,
        "resolver_contract_version": record.resolver_contract_version,
        "status": record.status,
        "assets": assets,
        "retry_after": (
            record.retry_after.isoformat() if record.retry_after is not None else None
        ),
        "reason": record.reason,
        "category_truncated": record.category_truncated,
        "attempt_count": record.attempt_count,
    }
```

In `cache.py`, use `canonical_record_payload(record)` when building
resolution snapshot entries. Do not change the detached `record_payload`
function or the cache schema.

- [ ] **Step 3: Run focused green checks.**

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest -q tests/unit/assets/test_cache.py --no-cov
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check src/osm_polygon_image_tag/assets/resolution.py src/osm_polygon_image_tag/assets/cache.py tests/unit/assets/test_cache.py
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ty check src tests
```

Expected: all commands exit 0, with canonical bytes, cache round-trips, and
snapshot identities unchanged.

### Task 2: Remove duplicate bytes encoding in cache writes

**Files:**
- Modify: `src/osm_polygon_image_tag/assets/cache.py`
- Test: `tests/unit/assets/test_cache.py`

- [ ] **Step 1: Add a stored-payload compatibility assertion.**

Extend the existing cache round-trip test to assert that the stored
`payload_json` equals `canonical_record_bytes(record).decode()` and that the
stored `response_sha256` equals `hashlib.sha256(canonical_record_bytes(record)).hexdigest()`.

- [ ] **Step 2: Hash bytes before decoding.**

In `ResolutionCache.put_many`, replace the repeated conversion with:

```python
payload_bytes = canonical_record_bytes(record)
payload_json = payload_bytes.decode()
values.append(
    (
        record.provider,
        record.canonical_reference,
        record.resolver_contract_version,
        record.status,
        payload_json,
        hashlib.sha256(payload_bytes).hexdigest(),
        record.retry_after.isoformat() if record.retry_after is not None else None,
    )
)
```

- [ ] **Step 3: Run focused checks and commit the implementation.**

```bash
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pytest -q tests/unit/assets/test_cache.py --no-cov
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ruff check src/osm_polygon_image_tag/assets/cache.py tests/unit/assets/test_cache.py
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run ty check src tests
git add src/osm_polygon_image_tag/assets/resolution.py src/osm_polygon_image_tag/assets/cache.py tests/unit/assets/test_cache.py
git commit -m "perf: avoid redundant resolution payload copies"
```

### Task 3: Benchmark and complete repository verification

**Files:** no additional source files unless a focused verification failure requires a correction.

- [ ] **Step 1: Run the deterministic asset benchmark before and after.**

Use the same 10,000-reference synthetic polygon workload and deterministic
registry. Record three fresh runs, median wall time, output SHA-256, and row
count. The current baseline median is 1.795990 seconds with output SHA
`a8ec6bdb9fbe4ae1ba0b218c9e68c1390011b7bf5a4a94fbfa6b822e409b3907` and
10,000 rows.

- [ ] **Step 2: Re-profile canonicalization.**

Run cProfile on the same asset build and verify the canonical path no longer
shows `copy.deepcopy` as required work for cache/snapshot serialization.

- [ ] **Step 3: Run all quality gates.**

```bash
MPLBACKEND=Agg MPL_IGNORE_SYSTEM_FONTS=1 MPLCONFIGDIR=/tmp/osm-polygon-image-tag-mplconfig UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just qa
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache uv run pre-commit run --all-files
UV_CACHE_DIR=/tmp/osm-polygon-image-tag-uv-cache just docs
just diff-review
```

If only sandbox DNS prevents packaging smoke from resolving `hatchling`, rerun
the unchanged `just smoke` command with approved network access. Treat any
code, test, coverage, mutation, packaging, documentation, or diff failure as
a blocker to commit/push.

- [ ] **Step 4: Review and push the clean branch.**

```bash
git diff --check
git status --short --branch
git log --oneline --decorate -4
git push origin main
git status --porcelain=v1 -b
git diff-tree --check --no-commit-id -r HEAD
```

Expected: the worktree is clean and `main` is aligned with `origin/main`.
