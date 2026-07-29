# Additional Image Tags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select and publish polygons carrying `bubbleid` or numeric indexed Panoramax tags and expose their values in a stable GeoParquet schema.

**Architecture:** Centralize target-tag recognition and Panoramax map construction in extraction helpers, then flow those values through transformation, Arrow schema, catalog statistics, and the generated card. Bump both compatibility versions so old shards are deterministically rebuilt.

**Tech Stack:** Python 3.12, Pyosmium, PyArrow/GeoParquet, SQLite, pytest, uv.

---

### Task 1: Target-tag matching

**Files:**
- Modify: `src/osm_polygon_image_tag/extraction.py`
- Modify: `src/osm_polygon_image_tag/_data/osmium-export.json`
- Test: `tests/test_extraction.py`
- Test: `tests/test_osmium_integration.py`

- [ ] Add failing tests asserting `bubbleid`, `panoramax`, and `panoramax:<digits>` match, while `panoramax:left`, `panoramax:`, and `panoramax:1:foo` do not.
- [ ] Run `uv run pytest tests/test_extraction.py tests/test_osmium_integration.py -q --no-cov` and confirm the new cases fail.
- [ ] Implement `is_target_tag_key()` using exact keys plus an ASCII-digit indexed Panoramax check; use it in `has_target_tag()` and configure Osmium export to retain candidate area objects.
- [ ] Rerun the focused tests and confirm they pass.

### Task 2: Stable GeoParquet representation

**Files:**
- Modify: `src/osm_polygon_image_tag/schema.py`
- Modify: `src/osm_polygon_image_tag/transform.py`
- Modify: `src/osm_polygon_image_tag/manifest.py`
- Test: `tests/test_schema.py`
- Test: `tests/test_transform.py`
- Test: `tests/test_storage.py`
- Test: `tests/test_pipeline.py`

- [ ] Add failing tests for nullable `bubbleid`, non-null sorted `panoramax_values`, and incompatibility with version-1 manifests.
- [ ] Run the focused tests and confirm failures describe missing columns or unchanged versions.
- [ ] Add the two fields, populate them deterministically, and increment `DATASET_SCHEMA_VERSION` and `PROCESSING_CONTRACT_VERSION` to 2.
- [ ] Rerun focused tests and confirm they pass.

### Task 3: Catalog and deterministic reporting

**Files:**
- Modify: `src/osm_polygon_image_tag/catalog.py`
- Modify: `src/osm_polygon_image_tag/reporting.py`
- Test: `tests/test_reporting.py`

- [ ] Add a failing real-shard statistics test containing exact Panoramax, multiple indexed Panoramax values, and `bubbleid`; assert one provider count per observation.
- [ ] Run `uv run pytest tests/test_reporting.py -q --no-cov` and confirm it fails on missing statistics/schema support.
- [ ] Extend provider masking to derive Panoramax presence from `panoramax_values`, add `bubbleid`, and update the generated card schema text.
- [ ] Rerun the focused tests and confirm they pass.

### Task 4: Documentation and readiness

**Files:**
- Modify: `README.md`
- Modify: `tests/fixtures/image_tag_coverage.osm`
- Test: `tests/test_end_to_end.py`

- [ ] Extend the real fixture and end-to-end assertions to prove both new tag forms survive extraction, publication inventory verification, and resume.
- [ ] Document the new columns and explain that the version bump rebuilds all old shards.
- [ ] Run `uv sync && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && git diff --check`.
- [ ] Review the final diff and run a read-only process/status check; do not signal the old pipeline.
- [ ] Commit, fast-forward to `main`, rerun the complete gate, and only then authorize restart.
