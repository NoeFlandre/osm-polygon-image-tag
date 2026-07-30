# `tests/unit/`

Fast, in-process tests grouped by the production subpackage they cover. Each
subdirectory mirrors the matching module package so a contributor can locate
the tests for any given responsibility at a glance.

## Markers

- Tests in this folder do not require the `osmium` executable. Run only this
  fast subset with `uv run pytest tests/unit -q --no-cov`.
- They may still exercise the full local pipeline through injected
  `Scanner`/`Exporter`/`Hub` callables.

## Coverage

- The default `pyproject.toml` threshold (`fail_under = 90`) is enforced
  by the root `pytest` configuration. Coverage is measured against
  `osm_polygon_image_tag` so reorganizing the package layout must preserve
  the existing test coverage.
