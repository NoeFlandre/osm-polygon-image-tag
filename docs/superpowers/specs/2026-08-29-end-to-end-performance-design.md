# End-to-End Performance Optimization Design

**Date:** 2026-08-29

## Goal

Reduce measured CPU and I/O overhead in the PBF-ingestion and asset-enrichment
hot paths without changing public APIs, serialized outputs, cache identity,
ordering, durability settings, resolver concurrency, or correctness.

## Baseline evidence

- The clean baseline passed all repository gates: 650 tests with 93.73% total
  coverage, 10 integration tests, 3 architecture tests, CRAP, mutation,
  packaging smoke, and diff checks.
- On the representative 10,000-way PBF, profiling identified per-record
  geometry/serialization work and tag-store writes as the main ingestion costs.
- Asset profiling identified repeated `dataclasses.asdict()` traversal and
  canonical resolution-record serialization as the largest avoidable CPU cost.
- An isolated equivalent payload builder was 58.9% faster than the current
  `asdict()` implementation while producing equal payloads and canonical JSON.

## Recommended design

### 1. Shape-preserving resolution payload construction

Replace the generic recursive dataclass traversal in
`assets/resolution.py:record_payload` with an explicit field mapping. Preserve
the existing dictionary keys, tuple shape for `assets`, ISO formatting for
`retry_after`, and detached asset values. Keep `canonical_record_bytes`, cache
digests, response digests, resolution snapshots, and asset rows on the same
canonical representation.

Regression coverage will verify exact payload equality against the prior
contract, canonical-byte equality, and that returned nested asset mappings are
detached from the source record.

### 2. Bounded PBF tag-store batch insertion

Add an internal `TagStore.add_many` operation that canonicalizes a bounded
sequence and uses one SQLite `executemany` call per commit interval. Update the
pipeline scanner callback to collect at most one commit interval of
`SourceTagRecord` values before calling `add_many`; flush the final partial
batch exactly as the current context does.

Keep `TagStore.add` as the immediate single-record API, including its duplicate
error behavior. Batch insertion remains bounded and keeps the existing
`synchronous=FULL` setting and commit cadence, so it changes call overhead only
and does not weaken crash durability.

### Explicitly deferred

Do not alter geodesic area algorithms, SQLite durability pragmas, provider
request concurrency, PBF processing order, Parquet schemas, or public artifact
checkpoint behavior. Those changes either lack a behavior-preserving proof or
would trade correctness/failure guarantees for uncertain gains.

## Verification

1. Run focused tests through a red-green cycle for payload equivalence and
   batched tag insertion.
2. Run Ruff, formatting, `ty`, focused tests, full pytest/coverage, acceptance,
   architecture, CRAP, mutation, packaging smoke, and diff checks.
3. Benchmark the same synthetic 10,000-way PBF before and after, asserting
   accepted/rejected counts and output Parquet bytes are identical.
4. Profile the post-change paths and stop if remaining safe candidates are
   marginal or would change the contracts listed above.
