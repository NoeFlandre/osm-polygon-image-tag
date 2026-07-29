# Phase 5 Exact Catalog, Statistics, and Dataset Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate byte-deterministic global statistics and a public Hugging Face dataset card solely from verified manifests and GeoParquet shards.

**Architecture:** A rebuildable SQLite catalog indexes only bounded Parquet batches and keys each indexed shard by output SHA-256. Changed shards are replaced transactionally, removed shards are reconciled, and unchanged shards are skipped. SQL aggregation produces exact counts, duplicates, tag combinations, geometry/type distributions, timestamp range, and area statistics; canonical JSON and a versioned template produce deterministic public files.

---

### Task 1: Incremental Rebuildable Catalog

- Create `catalog.py` with managed `catalog/catalog.sqlite`.
- RED tests cover first index, unchanged skip, changed replacement, removed shard
  removal, exact duplicate identities, and full rebuild equality.
- Stream only required Parquet columns in bounded batches; never load a shard or
  global identity set into memory.
- Commit `feat: add rebuildable exact dataset catalog`.

### Task 2: Canonical Artifact-derived Statistics

- Create `reporting.py` aggregating verified manifests and catalog rows.
- Include PBF/shard/row/byte counts, way/relation and geometry counts, provider
  counts and exact combinations, timestamp range, area sum/min/max/mean,
  rejection reasons, repeated identities, versions, and digests.
- Canonically serialize atomically to
  `statistics/dataset-statistics.json`.
- RED tests cover exact values, empty datasets, stable ordering, and byte
  identity on repeated generation.
- Commit `feat: generate exact artifact-derived statistics`.

### Task 3: Deterministic Public Dataset Card

- Add packaged `dataset-card-template.md` and generate data-root `README.md`.
- Card documents schema, current exact statistics, OSM/Geofabrik provenance,
  ODbL attribution, extraction/resume semantics, overlap behavior, intended
  uses, limitations, and the explicit image copyright/licensing/availability
  disclaimer.
- RED tests prove no current statistic is handwritten, unchanged artifacts
  yield byte-identical output, and required disclosures/field definitions are
  present.
- Commit `docs: generate factual public dataset card`.

### Task 4: Integrate Metadata into Local Run

- After each built or skipped shard, update catalog/statistics/card atomically.
- A metadata failure must fail the run and never alter shard/manifest validity.
- Add `rebuild-metadata` CLI command.
- Real synthetic run proves repeat generation is byte-identical.
- Run all uv/pytest/Ruff/mypy/diff gates and commit
  `feat: integrate deterministic dataset metadata`.
