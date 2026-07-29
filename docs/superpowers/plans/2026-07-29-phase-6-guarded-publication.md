# Guarded Hugging Face Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish only verified dataset artifacts to the intended Hugging Face dataset, incrementally and resumably, without ever publishing internal state.

**Architecture:** A pure publication planner builds a strict allowlisted inventory from verified manifests plus generated metadata. A small Hub adapter creates one commit, downloads the committed files for SHA-256 verification, and atomically records a receipt; the orchestrator invokes it after each completed PBF and metadata rebuild.

**Tech Stack:** Python 3.12, `huggingface_hub`, pytest, uv, Ruff, mypy.

---

### Task 1: Strict publication inventory

**Files:**
- Create: `src/osm_polygon_image_tag/publication.py`
- Modify: `src/osm_polygon_image_tag/errors.py`
- Test: `tests/test_publication.py`

- [ ] Write tests proving only verified `data/*.parquet`, matching `manifests/*.manifest.json`, `statistics/dataset-statistics.json`, and `README.md` are selected; reject symlinks, missing metadata, unexpected top-level entries, and hash mismatches while ignoring only a regular top-level `.DS_Store`.
- [ ] Run `uv run pytest tests/test_publication.py -q` and confirm failure because the publication API is absent.
- [ ] Implement immutable inventory records and a strict path-confined scanner using manifest verification.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Verified, resumable Hub commits

**Files:**
- Modify: `src/osm_polygon_image_tag/publication.py`
- Test: `tests/test_publication.py`

- [ ] Write failing tests for exact repository confirmation, dataset repo type, one bounded commit, remote SHA-256 verification through downloaded content, atomic receipts, receipt-based skipping, and no receipt on commit or verification failure.
- [ ] Run the focused test and verify the expected failures.
- [ ] Implement a narrow injectable Hub client protocol and publisher; never retry ambiguous writes.
- [ ] Run focused tests and confirm they pass.

### Task 3: Incremental orchestration and CLI

**Files:**
- Modify: `src/osm_polygon_image_tag/orchestrator.py`
- Modify: `src/osm_polygon_image_tag/cli.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Test: `tests/test_orchestrator.py`
- Test: `tests/test_cli_run.py`

- [ ] Write failing tests proving `run-and-publish` publishes after every processed shard, stops between PBFs, and requires `--confirm-repo NoeFlandre/osm-polygon-image-tag`; add `publish` for already-built artifacts.
- [ ] Run focused tests and verify expected failures.
- [ ] Add the publisher callback, CLI commands, and `huggingface_hub` dependency with the smallest required surface.
- [ ] Run focused tests and confirm they pass.

### Task 4: End-to-end readiness gate

**Files:**
- Modify: `README.md`
- Test: `tests/test_end_to_end.py`

- [ ] Add a failing synthetic end-to-end test covering PBF extraction, GeoParquet, manifest, deterministic metadata, fake publication, remote verification, receipt, and a second-run skip.
- [ ] Implement only integration glue needed to pass it.
- [ ] Document the exact preflight, local run, publish, and combined commands plus stop/resume semantics.
- [ ] Run `uv sync && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && git diff --check`.
- [ ] Run the exact read-only production preflight against the immutable source and separate data roots. Do not construct the production dataset or contact Hugging Face.
