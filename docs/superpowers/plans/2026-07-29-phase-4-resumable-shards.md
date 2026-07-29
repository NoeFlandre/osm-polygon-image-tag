# Phase 4 Resumable Per-PBF Shards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build each discovered PBF into one independently verified GeoParquet shard and atomic manifest, with bounded disk-backed tag lookup and exact resume decisions.

**Architecture:** A per-build SQLite tag store receives Pyosmium callbacks and provides indexed `(osm_type, osm_id)` lookups without unbounded memory. Canonical manifests bind source SHA-256, processing contract, output SHA-256/schema/rows, tool versions, counts, and rejection reasons. The orchestrator reuses a shard only when every identity and validation check passes.

**Tech Stack:** Python 3.12 standard library SQLite/hashlib/json, existing extraction/transformation/storage modules, pytest.

---

### Task 1: Disk-backed Exact Source Tag Store

- Create `tag_store.py` with a context-managed SQLite store under the configured
  data-root temporary namespace, primary key `(osm_type, osm_id)`, canonical
  JSON values, batched commits, lookup, exact count, and guaranteed cleanup.
- RED tests cover duplicate rejection, full Unicode/empty tag fidelity,
  callback ingestion, bounded batches, and cleanup after exceptions.
- Commit `feat: add disk-backed source tag store`.

### Task 2: Canonical Atomic Manifests

- Create `manifest.py` with versioned frozen source/output/count structures,
  chunked SHA-256, canonical JSON, atomic write/fsync/rename, strict parse, and
  complete identity validation.
- RED tests cover deterministic bytes, corrupt/unknown/missing fields, source
  drift, output drift, schema/contract drift, and no final manifest after write
  failure.
- Commit `feat: bind shards to canonical manifests`.

### Task 3: Build and Reuse One PBF

- Create `pipeline.py` implementing `build_one(PbfSource, PipelinePaths)`.
- Deterministic artifact names derive from the source relative path plus a
  collision-resistant digest.
- Build flow: fingerprint source; validate/reuse manifest+Parquet; populate
  temporary SQLite tag store; stream area export; restore exact tags; transform;
  count stable rejections; atomically write/validate Parquet; hash it; atomically
  write manifest; clean temporary state.
- RED tests inject scanner/exporter and cover build, skip, source drift,
  contract drift, corrupt output, iterator failure, zero-row shards, and exact
  rejection counts.
- Commit `feat: build and resume one verified PBF shard`.

### Task 4: Deterministic Multi-PBF Local Run and Graceful Stop

- Create `orchestrator.py` with deterministic discovery order, one active PBF,
  a `StopToken`, SIGINT/SIGTERM adapters, and structured progress results.
- Stop requests finish/abort only at documented safe boundaries and never claim
  incomplete progress.
- Add CLI `run` and `verify` commands; neither contacts a network.
- RED tests cover order, skip reuse, stop-before-next, failure stop, CLI exit
  codes, and no writes beneath source.
- Real-osmium integration builds the synthetic fixture twice and proves the
  second run skips byte-identical output.
- Run all uv/pytest/Ruff/mypy/diff gates.
- Commit `feat: orchestrate resumable local shard construction`.
