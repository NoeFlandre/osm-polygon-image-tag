# Phase 3 GeoParquet Transformation and Atomic Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform lossless image-tag area records into the approved typed row schema and atomically write validated, bounded-memory GeoParquet 1.1 shards.

**Architecture:** Pure transformation parses Osmium EWKB, validates Polygon/MultiPolygon geometry, computes OGC:CRS84 bounds and geodesic area, and produces exact provider columns plus the complete source tag map. Storage writes fixed-size Arrow batches to a same-filesystem temporary file, validates schema and GeoParquet metadata, fsyncs it, and atomically promotes it.

**Tech Stack:** Python 3.12, PyArrow 21.x, Shapely 2.1.x, PyProj 3.7.x, pytest, Ruff, mypy.

---

## Scope and Stop Condition

This phase implements transformation and one-shard storage only. It does not
add manifests, resumability decisions, global statistics, production CLI
commands, or publication. Stop after a real-osmium synthetic PBF produces and
validates one exact GeoParquet shard.

### Task 1: Define the Stable Arrow and GeoParquet Schema

**Files:**
- Create: `src/osm_polygon_image_tag/schema.py`
- Test: `tests/test_schema.py`
- Modify: `pyproject.toml`, `uv.lock`

- [ ] Add and lock `pyarrow>=21,<22`, `shapely>=2.1,<3`, and
  `pyproj>=3.7,<4`.
- [ ] Write a failing test for the exact approved column order and nullability:
  OSM identity/version metadata, source identity, WKB geometry/type, `area_m2`,
  four lon/lat bounds, complete `map<string,string>` tags, and six nullable raw
  provider strings.
- [ ] Assert GeoParquet 1.1 metadata, WKB encoding, OGC:CRS84 PROJJSON, and
  Polygon/MultiPolygon types.
- [ ] Implement `dataset_schema() -> pyarrow.Schema` and commit
  `feat: define image-tag GeoParquet schema`.

### Task 2: Transform and Reject Rows Deterministically

**Files:**
- Create: `src/osm_polygon_image_tag/transform.py`
- Test: `tests/test_transform.py`

- [ ] Write failing tests for Polygon and MultiPolygon EWKB, holes, exact raw
  target columns, full tag preservation, nullable metadata, deterministic
  `source_feature_id`, bounds, positive geodesic area, and stable timestamp UTC.
- [ ] Write failing tests for empty geometry, malformed WKB, non-polygon
  geometry, non-finite coordinates/area, and missing target tag; each yields a
  stable rejection code rather than a degraded row.
- [ ] Implement immutable `AcceptedRow` and `RejectedRow` outcomes plus
  `transform_record(record, source_pbf)`.
- [ ] Run focused/full gates and commit
  `feat: transform image-tag areas into typed rows`.

### Task 3: Write, Validate, and Atomically Promote Bounded Shards

**Files:**
- Create: `src/osm_polygon_image_tag/storage.py`
- Test: `tests/test_storage.py`

- [ ] Write failing tests proving fixed-size batches, Zstandard compression,
  deterministic row order, valid empty shards, map-tag fidelity, metadata
  preservation, rejection of schema drift, and no final file after iterator
  failure.
- [ ] Inject `batch_size` and a same-directory temporary-path factory for
  deterministic tests.
- [ ] Implement `write_geoparquet(rows, final_path, batch_size)` with
  `ParquetWriter`, fsync, independent validation, and `os.replace`.
- [ ] Implement `validate_geoparquet(path)` checking physical schema, required
  metadata, geometry encodings/types, and readable row groups.
- [ ] Run focused/full gates and commit
  `feat: atomically write validated GeoParquet shards`.

### Task 4: Real-osmium Synthetic End-to-End Shard

**Files:**
- Create: `tests/test_real_osmium_geoparquet.py`

- [ ] Convert the Phase 2 XML fixture to PBF with real `osmium`.
- [ ] Stream exact source tags and polygon export, restore tags, transform all
  accepted areas, and atomically write one shard.
- [ ] Assert exactly eight rows, exact way/relation identities, exact provider
  values, complete structural relation tags, positive area, valid bounds,
  Polygon/MultiPolygon geometry, GeoParquet 1.1 metadata, and zero nodes/open
  ways/`area=no` rows.
- [ ] Run the exact gates:

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
git diff --check
```

- [ ] Inspect the full diff and confirm no source-volume, generated-volume,
  sibling-repository, or remote mutation.
- [ ] Commit `test: prove real osmium to GeoParquet shard` and stop at Phase 3.
