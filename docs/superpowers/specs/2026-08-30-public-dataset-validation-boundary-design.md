# Public Dataset Validation Boundary Design

## Goal

Separate public-release validation and reuse checks from public-dataset
materialization without changing persisted artifacts, public APIs, or reuse
behavior.

## Current problem

`src/osm_polygon_image_tag/artifacts/public_dataset.py` owns four distinct
concerns: public polygon Parquet writing, release validation, manifest-based
reuse decisions, and polygon/asset orchestration. The validation and reuse
helpers are deterministic and have different dependencies from the builder,
but they are interleaved with materialization code. This makes the release
contract harder to locate and test independently.

## Design

Create `src/osm_polygon_image_tag/artifacts/public_dataset_validation.py` with
the existing implementations that define or validate the public release
contract:

- `public_polygon_schema` and the polygon schema/row validators;
- `validate_public_dataset` and its manifest/output validation helpers;
- manifest-backed polygon row-count reuse helpers;
- the public polygon `Manifest` constructor used by reuse and materialization.

Keep `public_dataset.py` responsible for the `PublicDatasetResult` value,
polygon row materialization, source orchestration, public manifest assembly,
cleanup, and the `build_public_dataset` entry point. Import the moved names
back into `public_dataset.py` so existing imports, private test seams, and
monkeypatch behavior remain compatible. The compatibility facade will not
change signatures or wrap calls, so stack behavior and exception types remain
unchanged.

The new module depends only on core manifest/serialization contracts, public
asset validators, and PyArrow. It will not import the builder, preventing a
cycle and making the release validation boundary independently testable.

## Invariants

- Public paths, schema version, field order, metadata, row counts, digests,
  manifest JSON, and cleanup behavior remain byte- and value-compatible.
- Reuse remains conservative: any missing, mismatched, malformed, symlinked,
  or invalid artifact causes a rebuild or validation error exactly as before.
- Existing imports from `public_dataset` continue to resolve, including
  private helpers currently used by the repository's tests.
- No new generic abstraction, cache, database change, or output format is
  introduced.

## Verification

The change follows red → green → refactor:

1. Add a direct ownership test for the new module and run it to observe the
   expected missing-module failure.
2. Move the existing implementation with the smallest possible compatibility
   import seam and run the focused public-dataset tests.
3. Refine names/imports and documentation only while the focused and full
   suites remain green.
4. Run the repository's complete `qa` and `ci` gates, including coverage,
   CRAP, mutation testing, packaging smoke, hooks, and strict documentation.
