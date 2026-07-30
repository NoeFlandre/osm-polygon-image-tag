# Credential-Aware Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh auth-limited enrichment results when credentials become available, without PBF reprocessing or secret persistence.

**Architecture:** Add non-secret provider capability queries to the resolver registry and use them at both manifest-reuse and resolution-cache boundaries. Give Commons a fixed descriptive user agent. Preserve the existing shard builder, cache schema, publication boundaries, and output schema.

**Tech Stack:** Python 3.12, uv, pytest, Ruff, ty, PyArrow, SQLite, httpx

---

### Task 1: Wikimedia request identity

**Files:**
- Modify: `src/osm_polygon_image_tag/resolvers/commons.py`
- Test: `tests/unit/resolvers/test_commons.py`

- [ ] **Step 1: Write the failing request-header test**

Add a recording metadata client and assert every Commons API call supplies:

```python
{
    "User-Agent": (
        "osm-polygon-image-tag/0.1.0 "
        "(https://github.com/NoeFlandre/osm-polygon-image-tag)"
    )
}
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
uv run pytest tests/unit/resolvers/test_commons.py -q --no-cov
```

Expected: failure because `_request` currently passes no headers.

- [ ] **Step 3: Add the fixed public user agent**

Define a module-level `_HEADERS` mapping in `commons.py` and pass it through
`MetadataClient.get_json` from `_request`. Do not add Wikimedia OAuth support.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the command from Step 2. Expected: all Commons tests pass.

### Task 2: Non-secret capability contract

**Files:**
- Modify: `src/osm_polygon_image_tag/resolvers/registry.py`
- Test: `tests/unit/resolvers/test_registry.py`

- [ ] **Step 1: Write failing capability tests**

Cover `public`, `anonymous`, and `credentialed` states for Mapillary, Flickr,
Commons, and providers without credentials. Empty environment values must count
as anonymous.

- [ ] **Step 2: Run the focused test and verify RED**

```bash
uv run pytest tests/unit/resolvers/test_registry.py -q --no-cov
```

Expected: the current page/direct strings and key-presence behavior fail.

- [ ] **Step 3: Implement the minimal capability API**

Make `capability(provider)` return only `public`, `anonymous`, or
`credentialed`, using truthy credential values for Mapillary and Flickr.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the command from Step 2. Expected: all registry tests pass.

### Task 3: Credential-aware manifest reuse

**Files:**
- Modify: `src/osm_polygon_image_tag/assets/build_state.py`
- Modify: `src/osm_polygon_image_tag/assets/builder.py`
- Test: `tests/unit/assets/test_build_state.py`
- Test: `tests/unit/assets/test_builder.py`

- [ ] **Step 1: Write failing manifest-reuse tests**

Assert a compatible shard is reusable anonymously, but not reusable when its
provider/status aggregates show Mapillary or Flickr page-only/auth-limited work
and that provider is credentialed. Assert unrelated providers still skip.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
uv run pytest tests/unit/assets/test_build_state.py tests/unit/assets/test_builder.py -q --no-cov
```

Expected: `reusable_manifest` has no capability input.

- [ ] **Step 3: Add the minimal reuse predicate**

Pass the registry's `capability` callable into `reusable_manifest`. Scan only
provider, status, and expiry columns in one pass and reject reuse only for
credential-improvable rows; keep schema and manifests unchanged.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all focused tests pass.

### Task 4: Credential-aware cache refresh

**Files:**
- Modify: `src/osm_polygon_image_tag/assets/builder.py`
- Test: `tests/unit/assets/test_builder.py`

- [ ] **Step 1: Write failing cache-refresh tests**

Seed cached Mapillary/Flickr `resolved_page_only` and `requires_auth` records.
Assert credentialed runs call the resolver and overwrite them, while anonymous
and unrelated stable results remain cache hits.

- [ ] **Step 2: Run the focused test and verify RED**

```bash
uv run pytest tests/unit/assets/test_builder.py -q --no-cov
```

Expected: the current `_resolve` returns all non-temporary cached records.

- [ ] **Step 3: Implement targeted refresh**

Extend the builder registry protocol with `capability(provider)`. Refresh
page-only records for credentialed Mapillary/Flickr and auth-required records
for credentialed providers. Do not modify the cache key or persist capability.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the command from Step 2. Expected: all builder tests pass.

### Task 5: Operator documentation and end-to-end proof

**Files:**
- Modify: `README.md`
- Modify: `docs/operations.md`
- Modify: `src/osm_polygon_image_tag/resolvers/README.md`
- Test: `tests/integration/test_end_to_end.py`

- [ ] **Step 1: Add a failing resume integration test**

Build anonymously, resume with a Mapillary token, and prove only the asset layer
is rebuilt from existing polygon Parquet while the resolver is called and the
direct result replaces page-only output.

- [ ] **Step 2: Run the integration test and verify RED**

```bash
uv run pytest tests/integration/test_end_to_end.py -q --no-cov
```

Expected: anonymous asset manifest is currently reused.

- [ ] **Step 3: Document every credential and exact resume command**

Document Mapillary dashboard creation, Flickr API key creation, Wikimedia's
no-token user-agent rule, Hugging Face login/`HF_TOKEN`, shell-safe environment
exports, secret-handling cautions, and that resumption does not reread PBFs.

- [ ] **Step 4: Run the integration test and verify GREEN**

Run the command from Step 2. Expected: all integration tests pass.

- [ ] **Step 5: Run the complete quality gate**

```bash
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pre-commit run --all-files
uv run pytest -q
uv build
git diff --check
```

Expected: every command succeeds, with no worktree changes produced by checks.

- [ ] **Step 6: Commit and push main**

```bash
git add README.md docs src tests
git commit -m "Make enrichment resume credential-aware"
git push origin main
```
