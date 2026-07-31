# `.github/workflows/`

GitHub Actions configuration for the repository. The CI workflow pins
third-party actions to immutable commit SHAs and runs the exact local quality
gates documented in [`docs/development.md`](../../docs/development.md).

## Contents

- `ci.yml`: the required quality workflow. It runs on every push to `main` and
  on every pull request targeting `main`, and on pushes to any `codex/**`
  branch.
- `docs.yml`: the GitHub Pages workflow. It runs on pushes to `main` and on
  manual dispatch, builds the strict MkDocs Material site, and deploys it to
  the `github-pages` environment.

## What the CI does

The single `quality-gate` job, on `ubuntu-latest`:

1. Check out the repository with `actions/checkout`.
2. Install `uv` and enable its built-in cache with `astral-sh/setup-uv`.
3. Install Python 3.12 via `uv python install`.
4. Install `osmium-tool` via `apt-get` so the integration tests can run
   against the real extractor.
5. Verify the lockfile matches `pyproject.toml` (`uv lock --check`).
6. Install the exact locked environment (`uv sync --locked --dev`).
7. Run `uv run ruff check .`.
8. Run `uv run ruff format --check .`.
9. Run `uv run ty check`.
10. Run `uv run pytest -q`.
11. Build the wheel and sdist (`uv build`).
12. Verify no whitespace errors (`git diff --check`).

The documentation workflow uses the same locked `uv` environment, runs
`uv run mkdocs build --strict --site-dir site`, uploads the Pages artifact, and
deploys it with the minimum Pages/OIDC permissions.

## What the CI does not do

- It does not access Seagate paths.
- It does not publish to Hugging Face.
- It does not require Hugging Face credentials.
- It does not run the production pipeline against any real data.
- It does not mutate GitHub state beyond running the workflow.

## Action pinning

Third-party actions are pinned to immutable commit SHAs with a readable
version comment on the same line. Bump the SHA and the comment together
when upgrading.
