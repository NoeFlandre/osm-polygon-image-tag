# `tests/`

Test suites for the `osm-polygon-image-tag` pipeline. The structure mirrors
the production package layout so each responsibility has a clear home for its
unit tests, while real-`osmium` and end-to-end tests live separately.

## Layout

```
tests/
  unit/            Fast, in-process tests grouped by production subpackage
    core/          PipelinePaths, errors, manifest, schema, progress
    ingest/        PBF discovery, osmium subprocess, tag store, transform
    artifacts/     storage, catalog, reporting, publication planner
    runtime/       pipeline, orchestrator, preflight, CLI
    integrations/  Hugging Face adapter and protocol
  integration/     Real osmium subprocess + end-to-end run
  fixtures/        Stable OSM XML fixtures used by integration tests
```

## What belongs in each folder

- `unit/`: tests that do not require `osmium` on `PATH` and run quickly.
  Use parameterization, monkeypatching, and in-memory fakes for external
  processes. The structure mirrors `src/osm_polygon_image_tag/`.
- `integration/`: tests marked with `@pytest.mark.integration`. They shell
  out to the real `osmium` binary and exercise the full pipeline end-to-end.
  The CI quality job installs `osmium-tool` and runs them as the explicit
  acceptance stage of `just qa`.
- `fixtures/`: small, stable OSM XML fixtures that drive the integration
  tests. They are committed and treated as immutable inputs.

## What must not be added here

- Generated parquet shards, sqlite databases, publication receipts, or
  anything that would be produced by running the pipeline for real. Those
  belong under the managed data root and must never appear in this folder.
- Long-running network calls to Hugging Face. The unit tests use an
  in-memory `Hub` protocol fake.

## Focused commands

```bash
uv run pytest tests/unit -q --no-cov
uv run pytest tests/integration -q --no-cov
uv run pytest -m integration -q --no-cov
just qa
```

## Why the split

- Fast unit tests stay fast because they never invoke `osmium`.
- The integration tests can be opted into explicitly when the operator
  wants to prove correctness against the real extractor.
- Mirroring the production layout makes the responsibility of each test
  file self-evident.
