# Development

How to set up the development environment, run the test suite, lint, type
check, build the wheel, and contribute changes.

## Toolchain

- `uv` for dependency management and the development virtualenv.
- `ruff` for linting and formatting.
- `ty` for type checking (the project migrated from `mypy` to `ty`).
- `pytest` with `pytest-cov` for tests and coverage.
- `hatchling` for the build backend.
- `osmium` for the integration tests that exercise the real extractor.
- `pre-commit` for local hooks and `just` for canonical recipes.

## Initial setup

```bash
git clone https://github.com/NoeFlandre/osm-polygon-image-tag.git
cd osm-polygon-image-tag
uv sync --locked --dev
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

`uv sync --locked` installs the exact lockfile-pinned environment,
including the development dependencies.

## Local quality gates

Run these before opening a pull request. They mirror the CI contract.

```bash
just check
just test
just build
```

`uv run pytest -q` runs the complete suite, including tests that require the
real `osmium` binary. For the fast unit-only loop, run:

```bash
uv run pytest tests/unit -q --no-cov
```

`just ci` runs the locked checks, repository-local pre-commit hooks, tests,
build, strict documentation build, and whitespace gate used by GitHub Actions.

Build the documentation site locally with:

```bash
just docs
```

The GitHub Pages workflow runs the same locked `mkdocs build --strict` command
on every push to `main` and can also be started manually from Actions.

## Smoke-testing the installed wheel

```bash
uv build
uvx --from dist/osm_polygon_image_tag-0.1.0-py3-none-any.whl osm-polygon-image-tag --help
```

The wheel must ship the packaged `osmium-export.json`. To confirm:

```bash
python -c "from importlib.resources import files; print(files('osm_polygon_image_tag').joinpath('_data/osmium-export.json').read_text())"
```

## Project layout

```
src/osm_polygon_image_tag/   # production package, organized by responsibility
tests/                       # unit/ + integration/ + fixtures/
docs/                        # current-facing documentation
.github/workflows/           # CI configuration
```

See [`architecture.md`](architecture.md) for the layering rules and
[`data-contract.md`](data-contract.md) for the schema and processing
contract.

## Adding or changing code

- Every behavioural change starts with a failing test. Add the test first,
  run it to confirm it fails for the right reason, then implement the
  minimum change to make it pass.
- Respect the layering rules in `architecture.md`. The dependency arrow
  flows downward; no upward imports are allowed.
- Do not weaken coverage. The configured minimum is 90%. Add focused
  regression coverage rather than lowering the threshold.
- When refactoring, run the focused tests first, then the full suite.
  Commit each independently green move separately.

## Type checking with `ty`

The project uses Astral's `ty` type checker. Configuration lives under
`[tool.ty]` in `pyproject.toml`; the entire `src` and `tests` trees are checked
without project-level diagnostic suppressions.

## Linting and formatting

`ruff` enforces `E`, `F`, `I`, `B`, `UP`, `SIM`, `RUF`, `S`, and `TID`.
Format with `uv run ruff format .` and let CI fail loudly if you forget.

## Commit and pull request conventions

- One independently green commit per logical change. A pull request may
  bundle several commits but each should leave the test suite green.
- Keep generated data, `.coverage`, caches, secrets, credentials, PBFs,
  Parquet files, SQLite databases, or Hugging Face receipts out of the
  repository. They are produced by running the pipeline against real data,
  not by editing the source.
- Pull requests run the same local gates plus the GitHub Actions CI. CI
  must be green before a merge.

## Licensing

- Pipeline source code: Apache-2.0 (see `LICENSE`).
- Dataset content: Open Database License (ODbL); see the generated
  `README.md` in the data root and the Hugging Face dataset card for
  attribution to the OpenStreetMap contributors.
