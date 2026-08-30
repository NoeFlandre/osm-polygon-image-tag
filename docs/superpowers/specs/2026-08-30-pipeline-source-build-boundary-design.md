# Runtime Source-Build Boundary Design

## Goal

Make the per-PBF runtime pipeline easier to read and test by separating source
materialization from resume/verification and manifest orchestration, without
changing any public API, artifact bytes, row ordering, counts, exceptions, or
dependency-injection seams.

## Current responsibility mix

`src/osm_polygon_image_tag/runtime/pipeline.py` currently owns both the
pipeline facade and the complete source-build transaction. `build_one` decides
whether an existing artifact is reusable, scans source tags into `TagStore`,
restores original tags from the exporter, transforms accepted/rejected rows,
writes GeoParquet, hashes the result, constructs a manifest, and returns the
public `BuildResult`.

That makes the facade difficult to scan and gives one function multiple reasons
to change. A stricter McCabe-5 review reports complexity 8 for `build_one`,
although the repository's configured gates currently accept it.

## Proposed boundary

Create `src/osm_polygon_image_tag/runtime/pipeline_build.py` with one focused
entry point, `build_source_output`, plus private helpers for:

- bounded tag scanning and flushing;
- restoring exported records and streaming transformation into GeoParquet;
- collecting the existing accepted/rejection counts while rows are written.

The new entry point returns the existing `WriteResult` and `RunCounts` types.
It receives the same source, paths, scanner, exporter, executable, and batch
size values that `build_one` already uses. It does not compute source/output
identity, decide reuse, or write the manifest.

Keep `runtime/pipeline.py` responsible for:

- `BuildResult`, artifact paths, reuse checks, and deep verification;
- source identity and output identity construction;
- manifest construction and persistence;
- the unchanged public `build_one` and `verify_one` entry points.

The pipeline facade imports `build_source_output` as `_build_source_output` so
the new implementation has a focused owner while the existing module remains
the only public runtime entry point. Existing tests that patch
`pipeline.TagStore.create` continue to work because the new module uses the
same imported `TagStore` class object.

## Non-goals

- no changes to source scanning, tag-store batching, exporter configuration,
  row transformation, Parquet serialization, hashing, manifest JSON, or
  cleanup behavior;
- no new runtime options, protocols, abstractions, or dependencies;
- no changes to public symbols or CLI behavior;
- no changes to mutation-test configuration.

## Acceptance evidence

- The ownership test fails before the new module exists and passes after the
  extraction.
- Existing runtime pipeline tests pass unchanged, including batch sizes,
  resume fast paths, deep verification, source drift, output corruption, and
  rejection counts.
- Ruff, format, ty, the full pytest suite, acceptance tests, architecture
  checks, CRAP, configured mutation testing, packaging smoke, pre-commit, and
  strict documentation build pass.
- `git diff --check` passes and local `main` equals `origin/main` after push.
