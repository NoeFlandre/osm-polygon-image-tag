# `tests/integration/`

End-to-end and real-`osmium` integration tests. Each test in this folder is
marked with `@pytest.mark.integration` and uses the small, committed
OSM XML fixtures in `tests/fixtures/`.

## Running locally

```bash
uv run pytest -m integration -q --no-cov
```

The tests shell out to the `osmium` executable resolved from `PATH`, so the
operator is responsible for installing `osmium-tool` (typically via
Homebrew) before invoking them. The default `uv run pytest -q` skips these
tests; run them explicitly when validating a release.

## Running in CI

GitHub Actions installs the official `osm` apt package, which provides
`osmium-tool`. The CI workflow installs it before running the integration
suite so the contract stays honest: if the binary is missing on a
contributor's machine, the integration tests fail loudly there too.

## What belongs here

- Tests that require `osmium cat`, `osmium export`, or other binaries.
- End-to-end exercises of the `run`/`run-and-publish`/`verify` CLI surface.
- Resume behaviour against the real extractor.

## What must not be added here

- Network calls to Hugging Face. Real publication is reviewed and run by
  hand, not from CI.
- Tests that mutate real data roots. Every integration test uses a
  per-test `tmp_path`.
