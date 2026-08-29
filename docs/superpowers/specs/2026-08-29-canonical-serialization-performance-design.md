# Canonical Serialization Performance Design

**Date:** 2026-08-29

## Goal

Reduce repeated asset-resolution serialization overhead without changing
payload fields, canonical JSON bytes, cache digests, snapshot identities, or
the detached-copy behavior of the existing `record_payload` function.

## Baseline evidence

- The pushed baseline is clean at `541ae4f` and passes the repository gates.
- A fresh 10,000-reference asset build has a median of 1.795990 seconds.
- The post-profile still spends measurable time in `deepcopy` through
  `record_payload` during cache writes, response digests, and resolution
  snapshots.
- PBF profiling is dominated by Shapely/Geod transformation and Parquet
  emission; transform batch-size trials produced no repeatable improvement and
  are deferred.

## Design

Keep `record_payload(record)` as the detached representation: it returns the
same nine keys, tuple-shaped `assets`, ISO-formatted `retry_after`, and deep
copies of nested asset mappings. Add one internal canonical payload helper that
references the immutable-in-use record fields without copying assets. Use that
helper only for read-only canonical JSON serialization and resolution snapshot
hashing. Canonical JSON never mutates its input, so this preserves the exact
serialized bytes while removing unnecessary allocation and traversal.

When writing cache rows, hash the canonical bytes directly before decoding
them to the SQLite text value. This removes one redundant UTF-8 encode/decode
round trip without changing the stored JSON or response SHA-256.

Do not alter cache schema, public payload semantics, record mutability,
resolver concurrency, PBF transform algorithms, Parquet schemas, or durability
settings.

## Verification

1. Add tests that preserve detached public payload behavior and compare the
   no-copy canonical path with the detached path byte-for-byte.
2. Monkeypatch `deepcopy` to fail and prove canonical record bytes and
   resolution snapshots do not copy assets.
3. Run the deterministic 10,000-reference asset benchmark and verify output
   SHA-256 and row counts remain identical.
4. Run the complete test, lint, type, coverage, mutation, packaging, docs, and
   diff gates before committing and pushing.
