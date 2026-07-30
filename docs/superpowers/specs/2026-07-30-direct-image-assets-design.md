# Direct Image Assets Enrichment Design

**Date:** 2026-07-30
**Status:** Approved design; implementation not started
**Project:** `NoeFlandre/osm-polygon-image-tag`

## 1. Purpose

Extend the existing OSM polygon image-reference dataset with directly usable
image assets while preserving every completed polygon shard and every raw OSM
tag. The enrichment must resolve provider identifiers and one-to-many
Wikimedia Commons categories into factual URLs without guessing relevance,
redownloading PBFs, or downloading image bodies.

The completed polygon dataset remains the immutable source layer. Enrichment
is an additive, separately versioned asset layer that can be stopped, resumed,
verified, refreshed, and published independently.

## 2. Current Evidence

The read-only profile taken on 2026-07-30 covered 217 completed polygon shards
and 172,698 rows. It found:

- 98,141 `wikimedia_commons` occurrences, including 68,693 category values and
  29,236 file values.
- 8,091 base/indexed Panoramax occurrences, including 7,954 UUID values.
- 22,993 Mapillary occurrences, most of which are IDs or non-URL labels.
- 58,312 `image` occurrences, mostly URLs but including page, video, embed,
  Commons, and malformed values.
- 431 Flickr, 81 KartaView, and 79 Bing Streetside `bubbleid` occurrences.

`Category:Brussels Park` was verified to return direct
`upload.wikimedia.org` URLs through the Wikimedia Commons category-members and
image-info APIs. Panoramax UUID
`4492cea4-1018-4285-8074-cf3d37f3c673` resolved through the Panoramax
metacatalog while returning 404 from a hard-coded origin instance. The
resolver therefore must use provider metadata rather than assume one host.

The public Hugging Face repository had 214 published polygon shards and
172,266 rows when inspected. Its Dataset Viewer API returned 500, so the
generated card must explicitly declare its configurations and data-file
patterns.

## 3. Scope

### Included

- Reuse finalized polygon Parquet shards as enrichment input.
- Read only polygon identity and provider-reference columns.
- Emit one row per resolved image asset.
- Emit one factual status row when a reference is unresolved, truncated,
  unavailable, invalid, unsupported, or pending retry.
- Resolve Commons files and direct category members.
- Resolve Panoramax IDs through the metacatalog.
- Resolve supported Mapillary, KartaView, Flickr, Bing Streetside, and generic
  `image=*` references under the contracts below.
- Cache positive and negative provider results.
- Resume at reference and asset-shard boundaries.
- Enrich future polygon shards automatically.
- Publish polygon and image-asset configurations to the same Hugging Face
  dataset.
- Preserve deterministic output ordering and data-derived public statistics.

### Excluded

- Downloading or redistributing image bytes.
- Reprocessing a completed PBF solely for enrichment.
- Recursive Commons category traversal.
- Choosing a subjective representative category image.
- Computer-vision relevance scoring or correspondence inference.
- Copyright, license, or safety inference.
- Image moderation.
- Proxying expiring provider URLs.
- Changing polygon geometry, source tags, or schema-v2 extraction semantics.

## 4. Artifact Model

The polygon contract remains dataset schema version 2. The asset contract
starts at asset schema version 1 and has an independent resolver contract
version.

Each finalized polygon shard:

```text
data/<stem>.parquet
manifests/<stem>.manifest.json
```

may produce:

```text
assets/<stem>.assets.parquet
asset-manifests/<stem>.assets.manifest.json
```

The asset manifest binds:

- source polygon path, size, SHA-256, and row count;
- asset schema and resolver contract versions;
- resolver-cache snapshot identity;
- output path, size, SHA-256, schema, and row count;
- provider, status, retry, truncation, and direct-URL counts.

An asset shard is reusable only when all bound identities and contract
versions match. An asset-contract change invalidates asset shards without
invalidating polygon shards.

## 5. Asset Row Contract

Every asset row contains:

| Column | Type | Null | Meaning |
|---|---|---:|---|
| `source_pbf` | string | no | Original source PBF relative path |
| `source_polygon_shard` | string | no | Polygon shard relative path |
| `osm_type` | string | no | `way` or `relation` |
| `osm_id` | int64 | no | OSM object ID |
| `osm_version` | int32 | yes | OSM version |
| `provider` | string | no | Canonical provider |
| `source_tag_key` | string | no | Exact source tag key |
| `source_tag_value` | string | no | Exact source tag value |
| `canonical_reference` | string | no | Provider-specific cache identity |
| `provider_asset_id` | string | yes | Provider-returned asset ID |
| `asset_index` | int32 | no | Stable zero-based order within a reference |
| `relation_kind` | string | no | Direct reference or category membership |
| `page_url` | string | yes | Stable human-viewable page |
| `image_url` | string | yes | Provider-returned direct image URL |
| `thumbnail_url` | string | yes | Provider-returned thumbnail URL |
| `image_url_expires_at` | UTC timestamp | yes | Provider-declared expiry |
| `mime_type` | string | yes | Provider/HTTP-returned MIME type |
| `width` | int32 | yes | Provider-returned width |
| `height` | int32 | yes | Provider-returned height |
| `license_id` | string | yes | Structured provider-returned license |
| `license_url` | string | yes | Structured provider-returned license URL |
| `author` | string | yes | Structured provider-returned author |
| `status` | string | no | Finite resolution status |
| `reason` | string | yes | Finite reason code |
| `category_truncated` | bool | no | Category exceeded configured cap |
| `retry_after` | UTC timestamp | yes | Earliest retry for temporary failure |
| `resolver_contract_version` | int32 | no | Resolver behavior version |
| `response_sha256` | string | yes | Digest of bounded canonical metadata |

Allowed statuses are:

- `resolved`
- `resolved_page_only`
- `not_direct_image`
- `category_empty`
- `category_truncated`
- `not_found`
- `private`
- `requires_auth`
- `invalid_reference`
- `unsupported`
- `temporary_failure`

The schema never claims that Commons category membership proves depiction of
the polygon. `relation_kind=category_membership` preserves that distinction.

## 6. Provider Contracts

### Wikimedia Commons

- `File:` and `Image:` values use `prop=imageinfo`.
- `Category:` values enumerate directly contained namespace-6 files using
  category-member pagination.
- Subcategories are not traversed.
- The default per-category cap is 500 files and is configurable.
- A truncated category emits its resolved asset rows plus a final
  `category_truncated` status row.
- Direct URL, description page, MIME type, dimensions, author, and license are
  emitted only when returned by structured API fields.

### Panoramax

- UUIDs are resolved through the Panoramax metacatalog.
- Origin, viewer, image, thumbnail, and metadata URLs come from returned links.
- No Panoramax instance hostname is assumed.
- Base and numeric indexed `panoramax:<n>` values are deduplicated by UUID per
  polygon while preserving every source key/value.

### Mapillary

- Numeric IDs always yield a stable Mapillary page URL.
- `MAPILLARY_ACCESS_TOKEN` enables Graph API lookup of current image metadata
  and thumbnail URLs.
- Without a token, numeric IDs produce `resolved_page_only`.
- Free-text labels that cannot identify an image produce
  `invalid_reference`.
- Temporary image URLs carry their returned or derived expiry when available.

### KartaView

- Sequence/index values use the public sequence/photo endpoint.
- The stable viewer URL and provider-returned image metadata are recorded.
- Provider instability is represented through bounded retries and explicit
  temporary or terminal statuses.

### Flickr

- Photo URLs and IDs are canonicalized to stable photo-page references.
- `FLICKR_API_KEY` enables `flickr.photos.getSizes`.
- The largest publicly permitted returned image is selected.
- Static image URLs are never guessed from partial IDs or secrets.
- Without a key, a valid public photo reference produces
  `resolved_page_only`.

### Bing Streetside `bubbleid`

- A stable Streetside/iD viewer reference is emitted when it can be
  constructed from the ID and polygon location.
- A raw Bing image URL is not fabricated because the supported Microsoft
  imagery API is coordinate- and key-based rather than a documented
  bubble-ID download API.
- Any future direct resolver requires its own resolver-contract version.

### Generic `image=*`

- Commons file/category values delegate to the Commons resolver.
- HTTP(S) values use the hardened bounded HTTP client.
- A URL is an `image_url` only when the final response factually identifies an
  image.
- HTML pages, videos, galleries, embeds, and cloud-share pages remain
  `page_url` values with `not_direct_image`.
- Malformed or unsupported values are preserved with explicit statuses.

## 7. Secure Network Boundary

OSM tag values are untrusted input. The shared HTTP client:

- permits only HTTP and HTTPS;
- rejects embedded credentials;
- rejects localhost, loopback, private, link-local, multicast, unspecified,
  documentation, and reserved destinations;
- validates DNS results before connection;
- validates every redirect target and prevents DNS rebinding;
- applies connect, read, total, and retry deadlines;
- honors bounded `Retry-After`;
- limits redirects, response headers, compressed and decompressed metadata
  size, and JSON nesting;
- reads no image body during resolution;
- redacts secrets and sensitive query parameters from logs and exceptions.

Provider credentials come only from environment variables. They never enter
Parquet, manifests, cache keys, receipts, statistics, or logs.

## 8. Cache and Determinism

The managed cache is SQLite and keyed by:

```text
provider + canonical_reference + resolver_contract_version
```

It stores canonical bounded response metadata, response digest, status,
attempt count, retry state, freshness policy, and output fields. SQLite uses
atomic transactions and a single-writer boundary. Positive and terminal
negative results are cached. Temporary results retain `retry_after`.

Duplicate references across overlapping Geofabrik extracts are resolved once.
Stable results do not refresh implicitly within a resolver contract. Expiring
provider URLs refresh shortly before publication without rereading polygons or
PBFs. Asset output is deterministic for a given polygon digest, resolver
contract, category cap, and cache snapshot; row ordering never depends on
network completion order.

## 9. Orchestration and Resumption

Resume performs these steps:

1. Discover compatible polygon manifests without hashing PBFs.
2. Reuse compatible polygon shards.
3. Discover and reuse compatible asset manifests.
4. Queue only missing, stale, incomplete, or refresh-eligible asset shards.
5. Continue sequential extraction of unprocessed PBFs.
6. Run one bounded network-oriented enrichment engine concurrently with
   extraction.
7. Enqueue every newly finalized polygon shard automatically.
8. Coalesce metadata regeneration and Hugging Face publication.
9. Stop at safe boundaries after one SIGINT or SIGTERM request.

Enrichment reads only polygon identity and provider columns in bounded Arrow
batches. It streams asset rows to an atomic temporary output. A crash discards
only that unfinished output; completed reference resolutions remain cached.
Resume rebuilds the unfinished asset shard from cache without repeating calls.

Progress reports polygon position, asset backlog, active asset shard,
reference totals, cache hits, provider rates, retries, cooldowns, category
expansion, truncation, publication state, and heartbeats. JSON remains
available for automation; interactive output never hides a long-running
stage.

## 10. Failure Semantics

Reference-level terminal outcomes become asset status rows and do not stop
unrelated work.

Temporary rate limits, timeouts, and 5xx responses use bounded retries and
cached retry state. A transparent temporary row may be published, but its
asset manifest remains refresh-eligible.

Path escape, symlink, corrupt cache, schema mismatch, digest mismatch,
malformed manifest, or unsafe network target stops the affected stage before
promotion or publication. Polygon shards remain untouched.

No exception or fallback may silently convert a reference into `resolved`.

## 11. Hugging Face Dataset Contract

The generated dataset card declares two configurations:

```yaml
configs:
- config_name: polygons
  default: true
  data_files:
  - split: train
    path: "data/*.parquet"
- config_name: image_assets
  data_files:
  - split: train
    path: "assets/*.assets.parquet"
```

The card documents join keys and loading examples. Deterministic statistics
derived from finalized artifacts report:

- polygon and asset shard/row totals;
- provider and status counts;
- direct-image, page-only, unresolved, retry, and truncation counts;
- cache-hit and network-resolution counts;
- stable and expiring URL counts;
- license and metadata coverage;
- duplicate polygon observations and duplicate assets;
- asset schema and resolver contract versions;
- category-membership and provider-availability limitations.

Publication inventory includes only compatible polygon data, asset data,
their manifests, generated statistics, and the generated card. It remains
non-destructive and fails closed on unknown files or symlinks.

## 12. Toolchain

- `uv` manages Python, dependencies, lockfile, and executable entry points.
- `ruff` formats and lints.
- `ty` type-checks source and tests; mypy is not used.
- `pytest` and `pytest-cov` enforce RED-to-GREEN tests and at least 90% branch
  coverage.
- `pre-commit` runs local Ruff and ty hooks; the full suite runs at pre-push.
- `typer` provides the typed CLI while preserving the existing six commands,
  option names, exit behavior, and automation compatibility.
- `rich` renders TTY tables, errors, and summaries.
- `tqdm` renders bounded enrichment/backfill meters only in interactive mode;
  it is disabled for JSON and non-TTY output.
- `just` defines canonical `sync`, `check`, `test`, `unit`, `integration`,
  `build`, `install-hooks`, and `ci` recipes.
- GitHub Actions pins Python, uv, Just, and third-party actions, then runs the
  lock check, pre-commit, Ruff, ty, full real-osmium tests, build, installed
  wheel smoke test, dataset-card validation, and whitespace gates.

CI uses reviewed provider fixtures and local HTTP servers. It performs no live
provider calls, Hugging Face writes, credential access, or Seagate access.

## 13. Testing Contract

Strict TDD covers:

- provider parsing, canonicalization, pagination, batching, deduplication, and
  finite statuses;
- exact asset Arrow and GeoParquet-compatible schema behavior;
- cache transactions, negative caching, retry state, resolver-version
  invalidation, and crash resumption;
- deterministic output under shuffled asynchronous completion;
- hostile HTTP targets, redirects, DNS rebinding, credential redaction,
  metadata limits, compression limits, and timeouts;
- migration from existing polygon shards with the PBF scanner replaced by a
  callable that raises if invoked;
- compatible asset reuse and asset-only invalidation;
- extraction/enrichment overlap, stopping, progress, and publication
  coalescing;
- publication allow-listing and remote reconciliation;
- installed CLI compatibility and packaged resources;
- generated Hugging Face YAML, statistics, loading examples, and card text.

Provider tests use minimal recorded responses. Integration tests use local
HTTP fixtures and the committed small OSM fixture. No production data or
network service is required by CI.

## 14. Production and Git Convergence

Implementation occurs in an isolated detached worktree while the production
process continues untouched. After independent review and complete green
gates:

1. Ask the operator to press Ctrl-C once and wait for normal return.
2. Verify the wrapper, Python process, and image-tag `osmium` child have exited.
3. Preserve the old main-checkout unstaged diff as an audit artifact; do not
   merge its unsafe deletion behavior.
4. Advance local and remote `main` by normal fast-forward without force push.
5. Verify GitHub Actions on `main`.
6. Remove temporary remote/local feature branches and worktrees only after
   proving `main` contains every required commit.
7. Ask the operator to resume with the unchanged production command.

The resumed command automatically performs fast polygon reuse, historical
asset backfill, remaining extraction, future enrichment, metadata generation,
and periodic publication. The raw PBF tree remains read-only.

## 15. Acceptance Criteria

- No completed compatible PBF is reprocessed for enrichment.
- The two concrete examples resolve into direct asset rows where the provider
  exposes them.
- Category expansion is one-to-many, direct-members-only, bounded, and
  transparently truncated.
- Unusable Mapillary labels remain explicit rather than guessed.
- Provider secrets and image bodies never enter project artifacts.
- Stop/resume repeats no completed provider resolution.
- Polygon schema and raw tags remain unchanged.
- Dataset Viewer exposes `polygons` and `image_assets`.
- The requested toolchain is installed, documented, exercised locally, and
  enforced in CI.
- Full tests, Ruff, formatting, ty, coverage, build, wheel smoke, card
  validation, and independent review pass.
- The final code history and remote repository use `main`; temporary branches
  are removed only after safe convergence.
