# Direct Image Assets Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a resumable one-to-many image-assets dataset that resolves existing raw provider references into factual page and direct-image URLs without reprocessing completed PBFs.

**Architecture:** Preserve polygon schema v2 and add independently versioned asset Parquet shards and manifests. Read only provider/identity columns from finalized polygon shards, resolve canonical references through a secure cached provider boundary, stream deterministic asset shards, and orchestrate historical backfill alongside remaining extraction. Publish explicit `polygons` and `image_assets` Hugging Face configurations.

**Tech Stack:** Python 3.12, uv, PyArrow/Parquet, SQLite, HTTPX, Typer, Rich, tqdm, pytest/pytest-cov, Ruff, ty, pre-commit, Just, GitHub Actions, osmium-tool.

---

## Working Rules

- Work in an isolated detached worktree based on remote `main`; do not edit
  `/Users/noeflandre/osm-polygon-image-tag` while its production process runs.
- Never read or mutate the Seagate data root from tests. Use `tmp_path`.
- Never call live providers from tests or CI. Use local HTTP fixtures and
  minimal reviewed JSON fixtures.
- Every behavioral task follows RED, observed failure, minimal GREEN, focused
  gate, then commit.
- Do not change polygon schema version 2 or processing contract version 2.
- Keep production modules under roughly 200 lines; split by responsibility
  when a file approaches that boundary.
- Preserve the six existing command names, option names, JSON summary output,
  exit code 2 for pipeline errors, and `progress {json}` automation format.

## File Map

New production packages:

```text
src/osm_polygon_image_tag/assets/
  README.md                 package ownership and contracts
  schema.py                 asset Arrow schema and finite enums
  manifest.py               asset manifest model and atomic I/O
  references.py             provider reference extraction/canonicalization
  cache.py                  transactional SQLite resolution cache
  storage.py                atomic asset Parquet writer/validator
  builder.py                polygon-shard to asset-shard state machine

src/osm_polygon_image_tag/resolvers/
  README.md                 provider boundary and safety rules
  types.py                  request/result protocols and dataclasses
  http.py                   hardened bounded async HTTP client
  commons.py                Commons file/category resolver
  panoramax.py              Panoramax metacatalog resolver
  mapillary.py              Mapillary page/API resolver
  kartaview.py              KartaView resolver
  flickr.py                 Flickr page/API resolver
  streetside.py             bubbleid page-only resolver
  generic.py                generic image URL classifier
  registry.py               provider dispatch and capability configuration

src/osm_polygon_image_tag/runtime/
  enrichment.py             bounded async enrichment engine and progress
```

New test areas mirror those packages under `tests/unit/assets/`,
`tests/unit/resolvers/`, and `tests/integration/`. Provider JSON fixtures live
under `tests/fixtures/providers/`.

## Task 1: Lock the Toolchain and Preserve CLI Compatibility

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `.pre-commit-config.yaml`
- Create: `Justfile`
- Modify: `.github/workflows/ci.yml`
- Test: `tests/unit/core/test_project_foundation.py`
- Test: `tests/unit/runtime/test_cli.py`

- [ ] **Step 1: Add failing project-toolchain assertions**

Add assertions that `pyproject.toml` contains production dependencies
`typer`, `rich`, `tqdm`, `httpx`, and `pyyaml`, development dependency `pre-commit`,
contains `ty` and no `mypy`, and that `.pre-commit-config.yaml` and `Justfile`
exist. Assert the current CLI help still lists exactly:

```python
EXPECTED_COMMANDS = {
    "preflight",
    "run",
    "run-and-publish",
    "publish",
    "verify",
    "rebuild-metadata",
}
```

- [ ] **Step 2: Run RED**

Run:

```bash
uv run pytest tests/unit/core/test_project_foundation.py \
  tests/unit/runtime/test_cli.py -q --no-cov
```

Expected: failure because the new dependencies and files are absent.

- [ ] **Step 3: Add the locked dependencies**

Use:

```bash
uv add "httpx>=0.28,<0.29" "typer>=0.16,<0.17" \
  "rich>=14,<15" "tqdm>=4.67,<5" "pyyaml>=6,<7"
uv add --dev "pre-commit>=4.3,<5"
```

Do not add mypy, aiohttp, requests, datasets, or an ORM.

- [ ] **Step 4: Create the local hooks**

Create `.pre-commit-config.yaml` with repository-local hooks that execute the
locked environment:

```yaml
repos:
- repo: local
  hooks:
  - id: ruff-check
    name: Ruff lint
    entry: uv run ruff check
    language: system
    types_or: [python, pyi]
  - id: ruff-format
    name: Ruff format check
    entry: uv run ruff format --check
    language: system
    types_or: [python, pyi]
  - id: ty
    name: ty
    entry: uv run ty check
    language: system
    pass_filenames: false
  - id: pytest
    name: pytest
    entry: uv run pytest -q
    language: system
    pass_filenames: false
    stages: [pre-push]
```

- [ ] **Step 5: Create canonical Just recipes**

Create `Justfile`:

```make
set shell := ["bash", "-euo", "pipefail", "-c"]

sync:
    uv sync --locked --dev

unit:
    uv run pytest tests/unit -q --no-cov

integration:
    uv run pytest tests/integration -q --no-cov

test:
    uv run pytest -q

check:
    uv lock --check
    uv run ruff check .
    uv run ruff format --check .
    uv run ty check

build:
    uv build

install-hooks:
    uv run pre-commit install --hook-type pre-commit --hook-type pre-push

ci:
    just check
    uv run pre-commit run --all-files
    just test
    just build
    git diff --check
```

- [ ] **Step 6: Make CI exercise Just and pre-commit**

Pin a Just setup action by full SHA, run `just ci`, retain installation of
`osmium-tool`, retain the installed-wheel smoke test added in Task 12, and keep
both committed-range whitespace checks.

- [ ] **Step 7: Run GREEN and commit**

Run:

```bash
just sync
just check
uv run pytest tests/unit/core/test_project_foundation.py \
  tests/unit/runtime/test_cli.py -q --no-cov
```

Expected: pass with unchanged CLI command set.

Commit:

```bash
git add pyproject.toml uv.lock .pre-commit-config.yaml Justfile \
  .github/workflows/ci.yml tests/unit/core/test_project_foundation.py \
  tests/unit/runtime/test_cli.py
git commit -m "build: standardize project toolchain"
```

## Task 2: Define the Asset Schema and Manifest Contract

**Files:**
- Create: `src/osm_polygon_image_tag/assets/__init__.py`
- Create: `src/osm_polygon_image_tag/assets/README.md`
- Create: `src/osm_polygon_image_tag/assets/schema.py`
- Create: `src/osm_polygon_image_tag/assets/manifest.py`
- Modify: `tests/unit/core/test_architecture.py`
- Create: `tests/unit/assets/README.md`
- Create: `tests/unit/assets/test_schema.py`
- Create: `tests/unit/assets/test_manifest.py`

- [ ] **Step 1: Write failing schema tests**

Define the exact columns from the approved design and assert:

```python
schema = asset_schema()
assert schema.names == EXPECTED_ASSET_COLUMNS
assert schema.field("osm_id").type == pa.int64()
assert schema.field("asset_index").nullable is False
assert schema.field("image_url_expires_at").type == pa.timestamp("ms", tz="UTC")
assert schema.metadata[b"osm_polygon_image_asset_schema_version"] == b"1"
```

Also parameterize every allowed status and reject an arbitrary string through
`validate_status`.

- [ ] **Step 2: Run schema RED**

Run:

```bash
uv run pytest tests/unit/assets/test_schema.py -q --no-cov
```

Expected: import failure because `assets.schema` does not exist.

- [ ] **Step 3: Implement the exact schema**

In `assets/schema.py`, define:

```python
ASSET_SCHEMA_VERSION = 1
RESOLVER_CONTRACT_VERSION = 1
ASSET_STATUSES = frozenset({
    "resolved", "resolved_page_only", "not_direct_image",
    "category_empty", "category_truncated", "not_found", "private",
    "requires_auth", "invalid_reference", "unsupported",
    "temporary_failure",
})

def asset_schema() -> pa.Schema: ...
def validate_status(value: str) -> str: ...
```

Use exactly the field names, types, and nullability from the approved
specification. Do not attach GeoParquet metadata because asset rows contain no
geometry.

- [ ] **Step 4: Write failing manifest round-trip and compatibility tests**

Use immutable dataclasses:

```python
@dataclass(frozen=True, slots=True)
class AssetSourceIdentity:
    relative_path: str
    size_bytes: int
    sha256: str
    row_count: int

@dataclass(frozen=True, slots=True)
class AssetRunCounts:
    rows: int
    statuses: dict[str, int]
    providers: dict[str, int]
    pending_retries: int
    truncated_categories: int
```

Test canonical byte equality across dict insertion orders, exact-key
validation, path escape rejection, atomic replacement cleanup, and independent
asset/resolver version rejection.

- [ ] **Step 5: Run manifest RED**

Run:

```bash
uv run pytest tests/unit/assets/test_manifest.py -q --no-cov
```

Expected: import failure because `assets.manifest` does not exist.

- [ ] **Step 6: Implement asset manifest I/O**

Define `AssetManifest`, `AssetSourceIdentity`, `ResolutionSnapshotIdentity`,
`AssetRunCounts`, `read_asset_manifest`, and `write_asset_manifest`. Reuse
`OutputIdentity` and `file_sha256` from `core.manifest`. Validate exact fields,
finite statuses, contract versions, and output containment before reuse.
`ResolutionSnapshotIdentity` is the deterministic digest of only the canonical
cache keys and resolution payloads used by this shard; it must not be a digest
of the global SQLite file, because unrelated cache writes must not invalidate
completed asset shards.

- [ ] **Step 7: Extend architecture enforcement**

Add layers:

```python
"assets": {"core", "assets"},
"resolvers": {"core", "assets", "resolvers"},
```

Allow runtime to depend on both. Artifacts may depend on assets for inventory
and reporting, but assets must not import artifacts, ingest, integrations, or
runtime.

- [ ] **Step 8: Run GREEN and commit**

Run:

```bash
uv run pytest tests/unit/assets tests/unit/core/test_architecture.py \
  -q --no-cov
uv run ruff check .
uv run ty check
```

Commit:

```bash
git add src/osm_polygon_image_tag/assets tests/unit/assets \
  tests/unit/core/test_architecture.py
git commit -m "feat: define image asset contracts"
```

## Task 3: Canonicalize Raw Provider References

**Files:**
- Create: `src/osm_polygon_image_tag/assets/references.py`
- Create: `tests/unit/assets/test_references.py`

- [ ] **Step 1: Write table-driven failing tests**

Use cases including:

```python
CASES = [
    ("wikimedia_commons", "Category:Brussels Park", "commons_category"),
    ("wikimedia_commons", "File:Jam1.jpg", "commons_file"),
    ("image", "Image:Example.jpg", "commons_file"),
    ("panoramax", "4492cea4-1018-4285-8074-cf3d37f3c673", "panoramax"),
    ("mapillary", "2627502594079174", "mapillary"),
    ("mapillary", "Site 1 In Zharey District", "invalid"),
    ("kartaview", "9010185/4", "kartaview"),
    ("flickr", "https://www.flickr.com/photos/user/6831725321", "flickr"),
    ("bubbleid", "215977408", "streetside"),
    ("image", "https://example.test/photo.jpg", "generic_http"),
]
```

Assert whitespace normalization never alters preserved `source_tag_value`.
Assert base/indexed Panoramax duplicate UUIDs produce one canonical request
but retain both source references.

- [ ] **Step 2: Run RED**

Run:

```bash
uv run pytest tests/unit/assets/test_references.py -q --no-cov
```

Expected: missing `assets.references`.

- [ ] **Step 3: Implement pure canonicalization**

Define:

```python
@dataclass(frozen=True, slots=True)
class SourceReference:
    provider: str
    source_tag_key: str
    source_tag_value: str
    canonical_reference: str
    resolver_kind: str

def references_from_row(row: Mapping[str, object]) -> tuple[SourceReference, ...]: ...
```

Use `urllib.parse`, strict ASCII UUID/decimal checks, Flickr path parsing, and
existing `panoramax_values`. Do not perform network calls or add provider URL
guesses in this module.

- [ ] **Step 4: Run GREEN and commit**

Run:

```bash
uv run pytest tests/unit/assets/test_references.py -q --no-cov
uv run ruff check src/osm_polygon_image_tag/assets tests/unit/assets
uv run ty check
```

Commit:

```bash
git add src/osm_polygon_image_tag/assets/references.py \
  tests/unit/assets/test_references.py
git commit -m "feat: canonicalize image references"
```

## Task 4: Build the Transactional Resolution Cache

**Files:**
- Create: `src/osm_polygon_image_tag/assets/cache.py`
- Create: `tests/unit/assets/test_cache.py`

- [ ] **Step 1: Write failing cache tests**

Test:

- schema creation under `data_root/cache/resolutions.sqlite`;
- exact key `(provider, canonical_reference, resolver_contract_version)`;
- canonical JSON and response SHA-256;
- positive and negative result reuse;
- temporary retry state;
- transaction rollback on injected failure;
- single-writer serialization from two threads;
- rejection of symlinked cache paths;
- no secret-bearing headers or query parameters in stored payloads.

Use a test result:

```python
ResolutionRecord(
    provider="panoramax",
    canonical_reference="4492cea4-1018-4285-8074-cf3d37f3c673",
    resolver_contract_version=1,
    status="resolved",
    assets=({"image_url": "https://cdn.test/picture.jpg"},),
    retry_after=None,
)
```

- [ ] **Step 2: Run RED**

Run:

```bash
uv run pytest tests/unit/assets/test_cache.py -q --no-cov
```

Expected: missing `assets.cache`.

- [ ] **Step 3: Implement the minimal SQLite store**

Use stdlib `sqlite3`, WAL mode, `synchronous=FULL`, foreign keys, explicit
transactions, deterministic JSON, and one process-local writer lock. Expose:

```python
class ResolutionCache:
    @classmethod
    def open(cls, data_root: Path) -> "ResolutionCache": ...
    def get(self, key: ResolutionKey) -> ResolutionRecord | None: ...
    def put(self, record: ResolutionRecord) -> None: ...
    def resolution_snapshot(
        self, keys: Sequence[ResolutionKey]
    ) -> ResolutionSnapshotIdentity: ...
    def close(self) -> None: ...
```

The snapshot hashes sorted requested keys and their canonical stored payloads,
not SQLite file bytes or unrelated rows. Do not implement TTL background
threads or a general task queue.

- [ ] **Step 4: Run GREEN and commit**

Run:

```bash
uv run pytest tests/unit/assets/test_cache.py -q --no-cov
uv run ruff check .
uv run ty check
```

Commit:

```bash
git add src/osm_polygon_image_tag/assets/cache.py \
  tests/unit/assets/test_cache.py
git commit -m "feat: add resumable resolution cache"
```

## Task 5: Enforce the Secure HTTP Boundary

**Files:**
- Create: `src/osm_polygon_image_tag/resolvers/__init__.py`
- Create: `src/osm_polygon_image_tag/resolvers/README.md`
- Create: `src/osm_polygon_image_tag/resolvers/types.py`
- Create: `src/osm_polygon_image_tag/resolvers/http.py`
- Create: `tests/unit/resolvers/README.md`
- Create: `tests/unit/resolvers/test_http.py`

- [ ] **Step 1: Write failing URL-policy tests**

Test rejection of:

```text
file:///etc/passwd
http://user:password@example.test/
http://127.0.0.1/
http://[::1]/
http://169.254.169.254/
http://10.0.0.1/
http://192.0.2.1/
```

Inject DNS results rather than relying on host networking. Test mixed
public/private answers, redirect-to-private, redirect loops, a host whose DNS
answer changes between validation and connection, oversized headers/JSON,
gzip expansion, timeout, `Retry-After`, and redacted exception text.

- [ ] **Step 2: Run RED**

Run:

```bash
uv run pytest tests/unit/resolvers/test_http.py -q --no-cov
```

Expected: missing resolver modules.

- [ ] **Step 3: Implement immutable resolver types**

Define `ResolvedAsset`, `ResolutionResult`, `ResolverContext`, and the
`Resolver` protocol. `ResolutionResult` must validate its status using
`assets.schema.validate_status`.

- [ ] **Step 4: Implement the hardened client**

Wrap `httpx.AsyncClient` behind:

```python
class SafeHttpClient:
    async def get_json(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> Mapping[str, object]: ...

    async def probe_image(self, url: str) -> ImageProbe: ...
```

Resolve and pin allowed IPs through an injected resolver/transport boundary,
validate each redirect, bound metadata bytes, disable automatic credential
leakage, and never call `.read()` for an image response. For unsupported
transport guarantees in the default HTTPX transport, implement a small
project-owned `httpx.AsyncBaseTransport` adapter that connects to the validated
IP while preserving the original hostname for TLS verification and HTTP
`Host`. Keep any `httpcore` use private to that adapter and cover it with the
DNS-change test; do not expose `httpcore` types in public project APIs.

- [ ] **Step 5: Run GREEN and commit**

Run:

```bash
uv run pytest tests/unit/resolvers/test_http.py -q --no-cov
uv run ruff check .
uv run ty check
```

Commit:

```bash
git add src/osm_polygon_image_tag/resolvers tests/unit/resolvers
git commit -m "feat: add secure provider HTTP boundary"
```

## Task 6: Resolve Wikimedia Commons and Panoramax

**Files:**
- Create: `src/osm_polygon_image_tag/resolvers/commons.py`
- Create: `src/osm_polygon_image_tag/resolvers/panoramax.py`
- Create: `tests/fixtures/providers/commons-file.json`
- Create: `tests/fixtures/providers/commons-category-page-1.json`
- Create: `tests/fixtures/providers/commons-category-page-2.json`
- Create: `tests/fixtures/providers/panoramax-picture.json`
- Create: `tests/unit/resolvers/test_commons.py`
- Create: `tests/unit/resolvers/test_panoramax.py`

- [ ] **Step 1: Write Commons RED tests**

Assert:

- `File:Jam1.jpg` emits one returned file;
- `Category:Brussels Park` paginates direct namespace-6 members in stable
  title/page-ID order;
- a 501-file fixture emits 500 assets plus a truncation result under the
  default cap;
- empty, missing, deleted, non-image MIME, malformed continuation, and
  structured license fields map exactly to the approved statuses;
- request batches use the maximum safe Wikimedia title batch rather than one
  request per file.

- [ ] **Step 2: Run Commons RED**

Run:

```bash
uv run pytest tests/unit/resolvers/test_commons.py -q --no-cov
```

Expected: missing `resolvers.commons`.

- [ ] **Step 3: Implement Commons resolver**

Use the MediaWiki Action API with `formatversion=2`, `prop=imageinfo`,
`iiprop=url|mime|size|extmetadata`, generator category-members,
`gcmtype=file`, and continuation. Parse only fields present in the response;
do not scrape HTML or infer license.

- [ ] **Step 4: Write Panoramax RED tests**

Assert the approved UUID calls the metacatalog, follows response metadata to
its origin links, emits viewer/image/thumbnail URLs, rejects mismatched IDs,
maps 404 to `not_found`, and never constructs an origin hostname.

- [ ] **Step 5: Run Panoramax RED**

Run:

```bash
uv run pytest tests/unit/resolvers/test_panoramax.py -q --no-cov
```

Expected: missing `resolvers.panoramax`.

- [ ] **Step 6: Implement Panoramax resolver**

Use the metacatalog picture endpoint and returned STAC/GeoVisio links.
Canonicalize the bounded response before digesting it. Do not download the
image or silently fall back to `panoramax.openstreetmap.fr`.

- [ ] **Step 7: Run GREEN and commit**

Run:

```bash
uv run pytest tests/unit/resolvers/test_commons.py \
  tests/unit/resolvers/test_panoramax.py -q --no-cov
uv run ruff check .
uv run ty check
```

Commit:

```bash
git add src/osm_polygon_image_tag/resolvers/commons.py \
  src/osm_polygon_image_tag/resolvers/panoramax.py \
  tests/unit/resolvers tests/fixtures/providers
git commit -m "feat: resolve Commons and Panoramax assets"
```

## Task 7: Resolve Remaining Providers Without Guessing

**Files:**
- Create: `src/osm_polygon_image_tag/resolvers/mapillary.py`
- Create: `src/osm_polygon_image_tag/resolvers/kartaview.py`
- Create: `src/osm_polygon_image_tag/resolvers/flickr.py`
- Create: `src/osm_polygon_image_tag/resolvers/streetside.py`
- Create: `src/osm_polygon_image_tag/resolvers/generic.py`
- Create: `src/osm_polygon_image_tag/resolvers/registry.py`
- Create: `tests/unit/resolvers/test_mapillary.py`
- Create: `tests/unit/resolvers/test_kartaview.py`
- Create: `tests/unit/resolvers/test_flickr.py`
- Create: `tests/unit/resolvers/test_streetside.py`
- Create: `tests/unit/resolvers/test_generic.py`
- Create: `tests/unit/resolvers/test_registry.py`
- Create: `tests/fixtures/providers/*.json`

- [ ] **Step 1: Write provider RED tests**

Cover:

- Mapillary numeric ID with and without `MAPILLARY_ACCESS_TOKEN`, free-text
  invalid references, deleted/private responses, and expiring thumbnails.
- KartaView sequence/index lookup and provider 200 responses carrying internal
  error codes.
- Flickr stable page parsing, `FLICKR_API_KEY` absence, returned-size
  selection, permission-limited originals, and invalid IDs.
- Streetside stable page-only link using polygon bbox center and no invented
  raw image URL.
- Generic direct image response, HTML page, video MIME, redirect, HEAD
  rejection followed by bounded GET metadata probe, and malformed URL.
- Registry concurrency/rate settings and missing-secret capability reporting.

- [ ] **Step 2: Run RED**

Run:

```bash
uv run pytest tests/unit/resolvers -q --no-cov
```

Expected: imports fail for the remaining provider modules.

- [ ] **Step 3: Implement minimal adapters**

Each provider module implements one `Resolver`. Keep URL parsing in
`assets.references`, HTTP safety in `resolvers.http`, and orchestration out of
provider modules. `registry.py` returns resolvers plus immutable per-provider
limits:

```python
ProviderLimit(max_concurrency=4, requests_per_second=2.0)
```

Use lower defaults for providers whose terms or returned headers require it.
Honor `Retry-After` over configured rates.

- [ ] **Step 4: Run GREEN and commit**

Run:

```bash
uv run pytest tests/unit/resolvers -q --no-cov
uv run ruff check .
uv run ty check
```

Commit:

```bash
git add src/osm_polygon_image_tag/resolvers tests/unit/resolvers \
  tests/fixtures/providers
git commit -m "feat: resolve supported image providers"
```

## Task 8: Stream and Reuse Asset Shards

**Files:**
- Create: `src/osm_polygon_image_tag/assets/storage.py`
- Create: `src/osm_polygon_image_tag/assets/builder.py`
- Create: `tests/unit/assets/test_storage.py`
- Create: `tests/unit/assets/test_builder.py`
- Create: `tests/integration/test_asset_backfill.py`

- [ ] **Step 1: Write storage RED tests**

Assert bounded batches, deterministic rows, Zstandard Parquet, exact schema,
atomic promotion, cleanup on failure, row-count validation, symlink rejection,
and preservation of an existing finalized asset when a replacement fails.

- [ ] **Step 2: Run storage RED**

Run:

```bash
uv run pytest tests/unit/assets/test_storage.py -q --no-cov
```

Expected: missing `assets.storage`.

- [ ] **Step 3: Implement asset storage**

Mirror the proven atomic pattern in `artifacts/storage.py`, but validate the
asset schema and do not attach GeoParquet metadata. Expose:

```python
def write_asset_parquet(
    rows: Iterable[Mapping[str, object]],
    final_path: Path,
    *,
    batch_size: int = 4096,
) -> AssetWriteResult: ...
```

- [ ] **Step 4: Write builder RED tests**

Build a tiny polygon Parquet fixture and inject a resolver. Assert:

- only identity, bbox, and provider columns are requested;
- no geometry or PBF path is opened;
- compatible asset manifest returns `skipped`;
- missing asset returns `built`;
- changed resolver version rebuilds only the asset;
- cache hits cause zero resolver calls;
- two source tags for one Panoramax UUID preserve provenance without duplicate
  network resolution;
- output order is polygon identity, provider, source key, canonical reference,
  provider asset ID, asset index;
- a stop leaves no finalized partial output.

- [ ] **Step 5: Run builder RED**

Run:

```bash
uv run pytest tests/unit/assets/test_builder.py -q --no-cov
```

Expected: missing `assets.builder`.

- [ ] **Step 6: Implement the per-shard state machine**

Define:

```python
@dataclass(frozen=True, slots=True)
class AssetBuildResult:
    status: Literal["built", "skipped", "pending"]
    polygon_shard: str
    asset_path: Path
    manifest_path: Path
    rows: int
    statuses: dict[str, int]

async def build_asset_shard(
    polygon_manifest: Manifest,
    polygon_path: Path,
    data_root: Path,
    *,
    cache: ResolutionCache,
    registry: ResolverRegistry,
    stop_requested: Callable[[], bool],
    progress: Progress,
) -> AssetBuildResult: ...
```

Resolve unique references through the cache, then stream rows in deterministic
source order. Finalize the asset manifest only after Parquet validation and
digest calculation. The callback keeps `assets` independent of `runtime`; the
orchestrator adapts its existing stop token with `stop_token.is_requested`.

- [ ] **Step 7: Prove no-PBF historical backfill**

In `tests/integration/test_asset_backfill.py`, create a real polygon shard
using the existing fixture, then delete or make the source PBF unreadable.
Inject a scanner/exporter that raises if called. Backfill the asset shard and
assert the polygon and manifest bytes and mtimes remain unchanged.

- [ ] **Step 8: Run GREEN and commit**

Run:

```bash
uv run pytest tests/unit/assets tests/integration/test_asset_backfill.py \
  -q --no-cov
uv run ruff check .
uv run ty check
```

Commit:

```bash
git add src/osm_polygon_image_tag/assets tests/unit/assets \
  tests/integration/test_asset_backfill.py
git commit -m "feat: build resumable asset shards"
```

## Task 9: Orchestrate Concurrent Backfill and Extraction

**Files:**
- Create: `src/osm_polygon_image_tag/runtime/enrichment.py`
- Modify: `src/osm_polygon_image_tag/runtime/orchestrator.py`
- Modify: `src/osm_polygon_image_tag/core/progress.py`
- Create: `tests/unit/runtime/test_enrichment.py`
- Modify: `tests/unit/runtime/test_orchestrator.py`
- Modify: `tests/unit/core/test_progress.py`

- [ ] **Step 1: Write scheduler RED tests**

Use injected builders and synchronization events to assert:

- historical compatible polygon manifests queue immediately;
- one enrichment engine overlaps one sequential extraction build;
- newly built polygons enter the asset queue;
- asset queue order is stable;
- configured provider concurrency is never exceeded;
- stop prevents new extraction and asset shards;
- an active asset shard may finish or remain pending without corrupt output;
- metadata/publication is coalesced after material finalized changes;
- an all-skipped polygon run still backfills missing assets;
- summary reports polygon and asset built/skipped/pending counts.

- [ ] **Step 2: Run RED**

Run:

```bash
uv run pytest tests/unit/runtime/test_enrichment.py \
  tests/unit/runtime/test_orchestrator.py -q --no-cov
```

Expected: missing enrichment scheduler and summary fields.

- [ ] **Step 3: Implement bounded enrichment runtime**

Use one background thread owning an asyncio event loop and one asset shard at a
time. Provider requests inside that shard use bounded async concurrency. The
main thread retains sequential `osmium` extraction. Communicate finalized
results through a bounded `queue.Queue`; never share SQLite connections across
threads.

Add an `EnrichmentSummary` nested in `RunSummary` rather than unrelated global
state. Coalesce publication after configurable finalized work count or elapsed
interval, with a final flush on normal completion. Drain finalized asset
results before the main thread takes the metadata/publication inventory
snapshot; background workers never invoke reporting or publication directly.

- [ ] **Step 4: Add explicit progress and heartbeat tests**

Assert events include:

```text
asset_backfill_started
asset_shard_started
asset_reference_progress
asset_provider_cooldown
asset_shard_completed
asset_backfill_completed
metadata_started
publication_started
```

Heartbeat must report the last event and both polygon/asset positions.

- [ ] **Step 5: Run GREEN and commit**

Run:

```bash
uv run pytest tests/unit/runtime tests/unit/core/test_progress.py \
  -q --no-cov
uv run ruff check .
uv run ty check
```

Commit:

```bash
git add src/osm_polygon_image_tag/runtime/enrichment.py \
  src/osm_polygon_image_tag/runtime/orchestrator.py \
  src/osm_polygon_image_tag/core/progress.py tests/unit/runtime \
  tests/unit/core/test_progress.py
git commit -m "feat: orchestrate automatic asset backfill"
```

## Task 10: Publish Two Factual Dataset Configurations

**Files:**
- Create: `src/osm_polygon_image_tag/artifacts/asset_inventory.py`
- Modify: `src/osm_polygon_image_tag/artifacts/catalog.py`
- Modify: `src/osm_polygon_image_tag/artifacts/statistics.py`
- Modify: `src/osm_polygon_image_tag/artifacts/dataset_card.py`
- Modify: `src/osm_polygon_image_tag/artifacts/reporting.py`
- Modify: `src/osm_polygon_image_tag/artifacts/publication_inventory.py`
- Modify: `tests/unit/artifacts/test_reporting.py`
- Modify: `tests/unit/artifacts/test_publication.py`
- Create: `tests/unit/artifacts/test_asset_inventory.py`

- [ ] **Step 1: Write asset inventory RED tests**

Assert compatible asset manifests and bound outputs are selected; old
contracts are managed but excluded; path escapes and size mismatches fail;
pending retry counts remain visible; no polygon or asset Parquet is rehashed
on the fast path.

- [ ] **Step 2: Run inventory RED**

Run:

```bash
uv run pytest tests/unit/artifacts/test_asset_inventory.py -q --no-cov
```

Expected: missing `artifacts.asset_inventory`.

- [ ] **Step 3: Implement asset inventory**

Follow `manifest_inventory.py` without merging polygon and asset manifest
types. Emit separate progress events and return
`list[tuple[AssetManifest, Path]]`.

- [ ] **Step 4: Write reporting and card RED tests**

Use tiny polygon and asset shards. Assert:

- statistics contain exact provider/status/direct/page-only/retry/truncation,
  expiry, metadata/license, duplicate, schema, and resolver counts;
- generated YAML parses and contains `polygons` default plus `image_assets`;
- glob paths are exactly `data/*.parquet` and `assets/*.assets.parquet`;
- card explains join keys and category-membership limitation;
- two identical artifact sets generate byte-identical JSON/card;
- handwritten statistics are absent.

- [ ] **Step 5: Run reporting RED**

Run:

```bash
uv run pytest tests/unit/artifacts/test_reporting.py -q --no-cov
```

Expected: missing asset statistics/configurations.

- [ ] **Step 6: Extend catalog/statistics/card**

Add rebuildable asset observations in separate SQLite tables. Do not copy URLs
into the polygon observation table. Generate YAML through structured Python
data and `yaml.safe_dump(..., sort_keys=False)` rather than string
concatenation of unescaped values. Build mappings in the documented stable
order and verify byte determinism in tests.

- [ ] **Step 7: Write publication RED tests**

Assert compatible asset shards/manifests are allow-listed, cache and temporary
state remain private, stale previously published asset paths become deletions,
symlinks fail before mutation, and unchanged polygon digests are not uploaded.

- [ ] **Step 8: Extend publication inventory**

Add only verified `assets/*.assets.parquet` and
`asset-manifests/*.assets.manifest.json`. Keep cache, catalog, retry state, and
receipts internal. Reuse manifest digests for large files.

- [ ] **Step 9: Run GREEN and commit**

Run:

```bash
uv run pytest tests/unit/artifacts -q --no-cov
uv run ruff check .
uv run ty check
```

Commit:

```bash
git add src/osm_polygon_image_tag/artifacts tests/unit/artifacts
git commit -m "feat: publish polygon and image asset configs"
```

## Task 11: Migrate the CLI to Typer with Rich and tqdm

**Files:**
- Modify: `src/osm_polygon_image_tag/cli.py`
- Create: `src/osm_polygon_image_tag/runtime/console.py`
- Modify: `tests/unit/runtime/test_cli.py`
- Modify: `tests/unit/runtime/test_cli_run.py`
- Create: `tests/unit/runtime/test_console.py`

- [ ] **Step 1: Expand failing CLI characterization**

Before changing the CLI, assert for all six commands:

- exact option names and required options;
- JSON summary on stdout;
- progress JSON on stderr;
- exit 2 and `error:` text for `ImageTagPipelineError`;
- `--help` exits 0;
- non-TTY and `--log-format json` contain no ANSI or progress bars;
- TTY human mode uses injected console/progress renderers;
- no provider secret appears in exceptions.

- [ ] **Step 2: Run characterization GREEN against argparse**

Run:

```bash
uv run pytest tests/unit/runtime/test_cli.py \
  tests/unit/runtime/test_cli_run.py -q --no-cov
```

Expected: existing compatibility tests pass; new human-mode tests fail because
the renderer does not exist.

- [ ] **Step 3: Implement the console boundary**

`runtime/console.py` owns:

- canonical JSON event rendering;
- Rich error/table/summary rendering for TTY human mode;
- tqdm asset-reference meters for TTY human mode;
- disabled tqdm and Rich styling for JSON/non-TTY mode.

No pipeline module imports Rich or tqdm.

- [ ] **Step 4: Replace argparse with Typer**

Create one `typer.Typer` app and six decorated commands. Retain the public
`run(argv, execute_*=...) -> int` injection surface by invoking the Typer app
through `CliRunner`-compatible argument handling or a thin adapter. Do not add
new required production options. Optional credentials remain environment-only.

- [ ] **Step 5: Run GREEN and commit**

Run:

```bash
uv run pytest tests/unit/runtime/test_cli.py \
  tests/unit/runtime/test_cli_run.py \
  tests/unit/runtime/test_console.py -q --no-cov
uv run osm-polygon-image-tag --help
uv run ruff check .
uv run ty check
```

Commit:

```bash
git add src/osm_polygon_image_tag/cli.py \
  src/osm_polygon_image_tag/runtime/console.py tests/unit/runtime
git commit -m "refactor: migrate CLI to Typer"
```

## Task 12: End-to-End Resume, Packaging, Documentation, and CI

**Files:**
- Modify: `tests/integration/test_end_to_end.py`
- Modify: `tests/integration/test_real_pipeline_resume.py`
- Create: `tests/integration/test_enriched_run_and_publish.py`
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Modify: `docs/architecture.md`
- Modify: `docs/data-contract.md`
- Modify: `docs/operations.md`
- Modify: `docs/development.md`
- Modify: every affected package/test `README.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/superpowers/plans/README.md`

- [ ] **Step 1: Write the full resume RED test**

Create three existing compatible polygon shards, two existing compatible asset
shards, and one missing asset shard. Inject:

```python
def forbidden_pbf_build(*_args: object, **_kwargs: object) -> NoReturn:
    raise AssertionError("completed PBF was reprocessed")
```

Run the combined command with a fake Hub and local provider server. Assert only
the missing asset is built, metadata has two configs, one publication occurs,
and the second run performs zero provider calls and zero uploads.

- [ ] **Step 2: Run RED**

Run:

```bash
uv run pytest tests/integration/test_enriched_run_and_publish.py \
  -q --no-cov
```

Expected: combined orchestration does not yet satisfy the full contract.

- [ ] **Step 3: Wire the final command path**

Ensure `run` and `run-and-publish` create the cache/registry, start enrichment,
backfill historical shards, enqueue new shards, flush metadata, and close all
threads, clients, and SQLite connections in `finally`.

- [ ] **Step 4: Update current-facing documentation**

Document:

- exact polygon and asset schemas;
- provider capabilities, required optional environment variables, and status
  meanings;
- Commons cap and non-depiction limitation;
- cache/internal state;
- unchanged resume command;
- progress events;
- safe stop behavior;
- two Hugging Face configurations and join example;
- Just and pre-commit workflows;
- no-PBF historical backfill guarantee.

Mark the approved design as implemented only after all gates pass.

- [ ] **Step 5: Add installed-wheel smoke tests**

Build a wheel, install it into a throwaway venv, and assert:

```text
osm-polygon-image-tag --help
all six subcommands
__version__ == 0.1.0
_data/osmium-export.json is readable
package README files are present
```

Add the same smoke sequence to CI.

- [ ] **Step 6: Validate Hugging Face metadata locally**

Parse the generated YAML with the library already used by
`huggingface_hub`. Assert both configuration globs match local fixture files.
Do not call the live Dataset Viewer in CI.

- [ ] **Step 7: Run the complete gate**

Run:

```bash
uv lock --check
uv sync --locked --dev
uv run pre-commit run --all-files
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest -q
uv build
git diff --check
```

Expected: all tests pass, branch coverage is at least 90%, wheel and sdist
build, and the worktree is clean after committing.

- [ ] **Step 8: Commit**

```bash
git add README.md CONTRIBUTING.md docs src tests .github/workflows/ci.yml \
  pyproject.toml uv.lock .pre-commit-config.yaml Justfile
git commit -m "docs: complete image asset enrichment"
```

## Task 13: Independent Review and Production Handoff

**Files:**
- Read only: `/Users/noeflandre/osm-polygon-image-tag`
- Read only: `/Volumes/Seagate M3/projects/osm-polygon-image-tag`
- Read only: `/Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw`
- Preserve: `/tmp/osm-image-tag-staging/main-checkout-unstaged.diff`

- [ ] **Step 1: Request independent review**

Ask a fresh reviewer to inspect the complete diff against the approved design,
with particular attention to SSRF/DNS rebinding, cache transactions,
deterministic output, no-PBF migration, stop behavior, secrets, publication
allow-listing, provider claims, and file size/separation. Address every
verified finding through RED→GREEN commits.

- [ ] **Step 2: Re-run the complete gate after review**

Run the exact Task 12 gate from a clean environment. Record the final test
count, coverage, built artifacts, commit SHA, and review disposition.

- [ ] **Step 3: Ask the operator for one graceful stop**

Provide:

```text
Press Ctrl-C once in the running image-tag terminal and wait for the command
to return. Do not send a second signal and do not kill osmium.
```

Do not proceed until `ps` confirms the wrapper, Python process, and its
image-tag `osmium` child are absent.

- [ ] **Step 4: Preserve and reconcile the dirty main checkout**

Verify the saved audit patch matches the two old unstaged files. The old patch
deletes temporary files without ownership proof and must not be applied.
Preserve the patch under `/tmp`; do not delete it. Restore only those exact two
tracked paths after confirming the new non-destructive inventory behavior is
already in committed history.

- [ ] **Step 5: Converge local and remote main**

Verify remote `main` is an ancestor of the implementation commit. Advance
local `main` and push with normal fast-forward only. Verify remote `main`
equals the reviewed commit. Delete temporary remote branches, local branches,
and worktrees only after `git merge-base --is-ancestor` proves containment.
Never force push.

- [ ] **Step 6: Verify GitHub Actions on main**

Wait for the exact `main` SHA workflow to complete successfully. Inspect logs
if any check fails, fix through TDD, and repeat the review/gate as warranted.

- [ ] **Step 7: Give the unchanged resume command**

Only after main and CI are green, tell the operator to run:

```bash
uv run osm-polygon-image-tag run-and-publish \
  --source-root "/Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw" \
  --data-root "/Volumes/Seagate M3/projects/osm-polygon-image-tag" \
  --confirm-repo NoeFlandre/osm-polygon-image-tag
```

Explain that the command will fast-skip compatible polygon shards, backfill
missing asset shards from Parquet/cache, continue remaining PBF extraction,
enrich new shards, regenerate two-config metadata, and publish periodically.
Do not run it on the operator's behalf unless separately authorized.
