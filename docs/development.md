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
- `h3` and `matplotlib` for the dataset-card geographic density map.
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

## Deterministic completion gate

Before declaring work complete, run the one canonical gauntlet. It is the same
command used by the pre-push hook and GitHub Actions:

```bash
just qa
```

The stages always run in this order and stop at the first failure:

```text
baseline → ruff → ty → tests → acceptance → architecture → CRAP → mutation → smoke → diff-review
```

`baseline` verifies the lockfile and recreates the locked development
environment. `ruff` runs lint and formatting checks; `ty` runs the type check;
`tests` runs the complete covered suite; `acceptance` runs the integration
tests explicitly; and `architecture` runs the import-layer guard directly.
`CRAP` requires every production function to remain below 6, while `mutation`
requires every generated mutant to be killed. `smoke` builds the wheel in an
isolated `/tmp` virtualenv and checks the CLI and packaged resources.
`diff-review` runs whitespace checks over working, staged, and committed
diffs. Coverage and mutation reports stay under `/tmp`.

`just ci` is the CI compatibility wrapper: it runs `just qa`, all-files
pre-commit validation, and the strict documentation build. The Docker smoke
job runs separately on GitHub Actions.

For the fast unit-only loop, run:

```bash
uv run pytest tests/unit -q --no-cov
```

`just unit` runs only the unit tests. `just acceptance` runs only the
integration/acceptance tests. These focused commands are for iteration; they do
not replace `just qa` before completion.

## Advanced test-quality checks

The advanced checks focus on the two highest-risk small boundaries: asset
catalog synchronization and Flickr size parsing. They do not process
production PBFs or data-root files.

```bash
just mutation
```

`just mutation` runs mutmut against those modules and their focused tests. The
scope is explicit so mutation testing stays practical on a developer laptop;
the configured run must leave no surviving mutants. The configuration excludes
only known equivalent mutations: SQL keyword casing, runtime-no-op typing casts,
and the return value of a progress fallback callback. These changes cannot
alter behavior, so testing them would add noise rather than protection.

```bash
just crap
```

`just crap` runs the full suite, writes a temporary LCOV report under `/tmp`,
and applies a strict CRAP score below 6 to every production function under
`src/`. CRAP combines cyclomatic complexity with test coverage, so a high score
identifies code that is both complicated and insufficiently exercised. The
temporary report is not part of the repository or the dataset.

Run both checks with `just quality-advanced`.

Build the documentation site locally with:

```bash
just docs
```

The GitHub Pages workflow runs the same locked `mkdocs build --strict` command
on every push to `main` and can also be started manually from Actions.

## Docker image smoke test

The production Dockerfile uses the pinned Python 3.12 slim Bookworm image,
`uv sync --locked --no-dev`, and Debian's pinned `osmium-tool` package. Build
and smoke-test it locally with:

```bash
docker build --tag osm-polygon-image-tag:0.1.0 .
docker run --rm --read-only --tmpfs /tmp osm-polygon-image-tag:0.1.0 --help
```

The Docker CI job performs the same build and `--help` check without mounting
PBFs, a data root, or Hugging Face credentials. The image contract is covered
by deterministic unit tests in `tests/unit/test_docker_contract.py`.

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
- Keep runtime result contracts in `runtime/results`; keep workflow
  coordination, source execution, and signal lifecycle in
  `runtime/orchestrator`. The orchestrator retains compatibility imports for
  existing callers.
- Keep immutable enrichment contracts in `runtime/enrichment_types`; keep
  worker lifecycle and concurrency in `runtime/enrichment`. The worker retains
  compatibility imports for existing callers.
- Keep public-asset responsibilities focused: use `public_asset_schema` for
  Arrow contracts and validators, `public_asset_checkpoint` for checkpoint
  policy, `public_asset_rows` for deterministic row transformation and
  bounded-batch deduplication, `public_asset_accumulator` for SQLite
  persistence and provenance, and `public_assets` for source orchestration
  and output assembly.
- Use `public_polygon_accumulator` for SQLite polygon selection, provenance,
  and checkpoint persistence; use `public_dataset_validation` for release
  schema, manifest, digest, row-count, and reuse checks; keep materialization
  and orchestration in `public_dataset`.
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
