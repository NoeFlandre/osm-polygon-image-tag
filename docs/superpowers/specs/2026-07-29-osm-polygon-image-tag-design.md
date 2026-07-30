# OSM Polygon Image-Tag Dataset Pipeline Design

**Date:** 2026-07-29  
**Status:** Historical. See [`docs/data-contract.md`](../../data-contract.md) for
the current contract and
[`2026-07-29-additional-image-tags-design.md`](2026-07-29-additional-image-tags-design.md)
for the additive schema-v2 fields (`bubbleid`, `panoramax_values`).  
**Project:** `NoeFlandre/osm-polygon-image-tag`

## 1. Purpose

Build a public, independently versioned Python pipeline that extracts OSM area
features carrying image-reference tags from Geofabrik PBF files and publishes a
resumable GeoParquet dataset to Hugging Face.

The pipeline preserves lossless source information needed by later,
provider-specific image-processing projects. It does not resolve, normalize,
validate, or download image references.

## 2. Project and Storage Boundaries

- Code repository: `/Users/noeflandre/osm-polygon-image-tag`
- Generated-data root:
  `/Volumes/Seagate M3/projects/osm-polygon-image-tag`
- Read-only PBF source:
  `/Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw`
- GitHub repository: `NoeFlandre/osm-polygon-image-tag`
- Hugging Face dataset: `NoeFlandre/osm-polygon-image-tag`

This project owns its package, tests, configuration, documentation,
`pyproject.toml`, `uv.lock`, and Git history. It must not modify, import from,
symlink to, or share runtime state with another local project. Proven patterns
may be copied during initialization, but copied code becomes independently
owned and maintained here.

The raw PBF tree is immutable input. Preflight must reject overlapping source
and output paths, unsafe symlinks, path traversal, or any configuration that
could write beneath the source tree.

## 3. Scope

### Included

- Nodes: never included.
- Ways: only closed ways recognized as areas under standard OSM polygon rules.
- Relations: only relations that construct valid area geometry, including
  valid multipolygon and boundary relations.
- Geometry: Polygon and MultiPolygon only.
- Tag condition: the emitted way or relation itself contains at least one of
  `image`, `wikimedia_commons`, `mapillary`, `panoramax`, `kartaview`, or
  `flickr`.
- Every accepted row retains all original tags and exact raw target values.

### Excluded

- Nodes, open ways, linear features, route relations, and non-area relations.
- Emitting a member way merely because its parent relation matches.
- Matching a relation merely because one of its members has a target tag.
- Provider API calls, URL normalization, link validation, image downloads,
  licensing inference, content moderation, and correspondence checks.
- Contributor username/UID, guessed URLs, centroids, and provider-specific
  parsed fields.
- Shared libraries, submodules, monorepo integration, or cross-project writes.

## 4. Architecture

The independent `uv` project has focused modules:

1. Configuration and boundaries validate paths, contracts, and limits.
2. Discovery recursively enumerates PBFs in stable relative-path order.
3. Extraction invokes `osmium` to stream standard OSM area geometries.
4. Transformation validates rows, preserves tags, computes geodesic area and
   bounds, and emits bounded Arrow batches.
5. Storage writes GeoParquet and manifests through atomic promotion.
6. Catalog and reporting maintain rebuildable exact global statistics and
   deterministic public metadata.
7. Orchestration executes a per-PBF resumable state machine.
8. Publication creates and verifies guarded Hugging Face commits.
9. The CLI exposes explicit local, publication, verification, and combined
   workflows.

Modules communicate through typed contracts and have no dependency on another
project's package or data cache.

## 5. Data Contract

Each row is one observation of one OSM object in one source PBF. Overlapping
Geofabrik extracts remain lossless: repeated objects remain separate
observations and are measured in global statistics.

| Column | Type | Null | Meaning |
|---|---|---:|---|
| `osm_type` | string enum | no | `way` or `relation` |
| `osm_id` | int64 | no | OSM object ID |
| `osm_version` | int32 | yes | OSM version when present |
| `osm_changeset` | int64 | yes | OSM changeset when present |
| `osm_timestamp` | timestamp UTC | yes | Feature timestamp when present |
| `source_pbf` | string | no | Stable relative source identifier |
| `source_feature_id` | string | no | Deterministic source/type/ID/version identity |
| `geometry` | WKB binary | no | Full Polygon or MultiPolygon |
| `geometry_type` | string enum | no | `Polygon` or `MultiPolygon` |
| `area_m2` | float64 | no | Geodesic square metres |
| `bbox_min_lon` | float64 | no | Western bound |
| `bbox_min_lat` | float64 | no | Southern bound |
| `bbox_max_lon` | float64 | no | Eastern bound |
| `bbox_max_lat` | float64 | no | Northern bound |
| `tags` | map<string,string> | no | Complete original OSM tag map |
| `image` | string | yes | Exact raw target value |
| `wikimedia_commons` | string | yes | Exact raw target value |
| `mapillary` | string | yes | Exact raw target value |
| `panoramax` | string | yes | Exact raw target value |
| `kartaview` | string | yes | Exact raw target value |
| `flickr` | string | yes | Exact raw target value |

At least one target column is non-null. An OSM empty string remains an exact
value rather than being silently converted to null.

GeoParquet uses version 1.1 metadata, WKB, OGC:CRS84 coordinate order, and
Zstandard compression. Area is geodesic and accounts for holes and all
multipolygon parts. Row order within a shard is deterministic.

The processing contract covers schema, extraction configuration, geometry
rules, transformation semantics, and software version. An incompatible contract
change invalidates only affected artifacts.

## 6. Extraction and Transformation

For each PBF:

1. Fingerprint the source using relative identity, size, and cryptographic
   digest without modifying it.
2. Stream area features through `osmium`.
3. Reject nodes, open ways, and non-area relations.
4. Require a target tag on the emitted object itself.
5. Preserve all tags and exact target values.
6. Validate Polygon/MultiPolygon geometry.
7. Compute geodesic area and bounds.
8. Accumulate exact counters while writing bounded Arrow batches.
9. Validate temporary Parquet and manifest artifacts.
10. Atomically promote both.

Malformed matching features are excluded from the main dataset. The manifest
records exact rejection counts under stable reason codes. A malformed row does
not fail an otherwise valid shard; corrupt sources, broken extraction, contract
violations, invalid finalized artifacts, or systemic failures do.

## 7. Managed Artifacts

Generated state is confined to documented namespaces:

- `data/`: one GeoParquet shard per source PBF
- `manifests/`: one canonical manifest per finalized shard
- `statistics/`: canonical global statistics
- `catalog/`: rebuildable local disk-backed indexes
- `receipts/`: verified local publication receipts (originally sketched as
  `publication/` in this document; the implementation stores them under
  `receipts/publication.json`)
- `tmp/`: incomplete project-owned artifacts
- `README.md`: generated Hugging Face dataset card

Source relative paths map deterministically to collision-resistant artifact
paths. Unexpected top-level entries, symlinks, special files, and path escapes
fail closed during publication planning. Any platform metadata exception must
be narrow, explicit, and tested.

## 8. Resumability and Stoppability

Each PBF follows:

```text
discovered -> processing -> locally verified -> metadata regenerated
           -> published -> remotely verified
```

- Final artifacts appear only through atomic rename.
- Source fingerprint plus processing-contract version keys reuse.
- Valid finalized shards are not recomputed.
- Changed input or contract invalidates only its shard.
- `SIGINT` and `SIGTERM` request a stop at a safe batch boundary.
- An incomplete shard is never promoted.
- Hard-kill residue is confined to managed temporary locations.
- Resume safely replaces stale temporary artifacts.
- Completed local artifacts remain reusable if publication fails.

No checkpoint may claim progress that is not independently verifiable from
final artifacts or verified remote state.

## 9. Publication

The periodic unit is one completed PBF. Skipped PBFs do not trigger
metadata regeneration or publication in the current implementation (this
document originally suggested publishing after every PBF regardless; the
current contract publishes only after a shard is newly built, so a resume
that only skips verified PBFs does not re-publish):

1. Verify finalized Parquet and manifest.
2. Rebuild or incrementally verify the global catalog.
3. Regenerate deterministic statistics and the dataset card.
4. Construct a strict managed-file upload plan.
5. Publish shard, manifest, statistics, and card in one Hugging Face commit.
6. Verify remote paths, sizes, and digests.
7. Atomically record a publication receipt under `receipts/`.
8. Continue to the next PBF.

An interrupted upload is reconciled with actual Hugging Face state. Matching
remote files are reused; missing or mismatched managed artifacts are uploaded.
Remote state is never inferred solely from a local receipt.

Processing and publication remain independently callable and reviewable. Tests
must not perform real uploads. Real PBF processing, Hugging Face publication,
GitHub mutation, and pushes each require separate approval.

## 10. Statistics and Dataset Card

Statistics are exact, artifact-derived, canonical, and stably ordered:

- discovered, completed, and published PBF counts;
- total rows and shard bytes;
- ways versus relations and Polygon versus MultiPolygon;
- counts for each target tag and exact tag combinations;
- minimum and maximum feature timestamp;
- total, minimum, maximum, and arithmetic-mean area;
- accepted and rejected counts by reason;
- duplicate `(osm_type, osm_id, osm_version)` observations;
- repeated OSM identities across PBFs;
- schema, processing-contract, source, and artifact digests.

The disk-backed catalog supports exact duplicate accounting without unbounded
memory. It is rebuildable cache state, never authoritative hidden state.

The generated Hugging Face card documents summary, schema, coverage, exact
current statistics, methods, intended uses, exclusions, OSM/Geofabrik
provenance, ODbL attribution, reproducibility, limitations, overlap semantics,
and known biases. It warns that references may be stale or inaccessible and
that inclusion does not establish image copyright, licensing, safety,
availability, or correspondence. Unchanged artifacts produce byte-identical
statistics and card output.

## 11. Public CLI

- `preflight`: validate tools, paths, inventory, capacity, and optionally
  Hugging Face access without mutation.
- `run`: process or resume locally without network publication.
- `publish`: publish or resume finalized artifacts without processing PBFs.
- `run-and-publish`: process one PBF, publish and verify it, then continue.
  Skipped PBFs do not trigger a publish; only newly built PBF cycles do.
- `verify`: validate local artifacts and optionally compare managed remote
  artifacts.
- `rebuild-metadata`: rebuild catalog, statistics, and card from shards.

Defaults are safe and explicit. Destructive reconciliation is limited to known
project-owned temporary or managed output paths. Source files and unknown
entries are never deleted.

## 12. Errors and Retries

- Malformed row geometry: record rejection and continue.
- Source corruption or extractor failure: fail current shard.
- Schema, manifest, or artifact failure: fail current shard.
- Unsafe path or boundary violation: fail before processing.
- Authentication or authorization failure: fail without retry.
- Transient network timeout or service error: bounded retry with backoff.
- Remote verification mismatch: fail without recording completion.

Data and contract failures are never retried blindly. Diagnostics redact
credentials and bound subprocess output.

## 13. TDD and Verification

Every behavior is a small RED -> GREEN slice:

1. Add a focused failing test and observe the expected failure.
2. Implement the minimum behavior needed for green.
3. Refactor only while all tests remain green.

Coverage includes tag matching and preservation; node/open-way/non-area
exclusion; standard closed-way and valid relation inclusion; relation/member
tag ownership; schema, geometry, area, and bounds; bounded streaming; atomic
promotion and interruption recovery; source and contract drift; corrupt
artifacts; path boundaries; guarded publication and reconciliation;
deterministic reporting; exact disk-backed duplicate accounting; real-`osmium`
synthetic integration fixtures; and exact `uv` packaging.

Final local gates:

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
```

A coverage threshold is enforced. Passing mocks are not evidence of real PBF
correctness or remote publication; those need separate verification gates.

## 14. Delivery Phases

Work proceeds one reviewed phase at a time:

1. Foundation and immutable path boundaries
2. Extraction contract
3. GeoParquet transformation and atomic storage
4. Resumability and verification
5. Exact reporting and dataset card
6. Guarded Hugging Face publication
7. Read-only real-source preflight
8. Separately approved real processing
9. Separately approved publication

Each implementation phase stops after its tests and local verification are
reviewed. No phase authorizes the next phase or remote mutation.

## 15. Acceptance Criteria

- The repository is independent and uses `uv`.
- The PBF tree is demonstrably read-only to the pipeline.
- Only matching closed area ways and valid area relations are emitted.
- Nodes, open ways, and non-area relations are absent.
- All original tags and exact raw provider values are preserved.
- Output is GeoParquet 1.1 with full geometry and geodesic area.
- Processing is bounded-memory, deterministic, stoppable, and resumable at
  finalized-shard granularity.
- Per-PBF publication is guarded, resumable, and remotely verified.
- Statistics and the card are exact, deterministic, and artifact-derived.
- Cross-PBF duplicates remain observable and are quantified.
- Invalid rows are counted by reason without degrading accepted rows.
- All automated local quality gates pass.
- Real data and remote operations happen only after explicit approval.
