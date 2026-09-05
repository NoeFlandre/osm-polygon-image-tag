# `.github/workflows/`

GitHub Actions configuration for the repository. The CI workflow pins
third-party actions to immutable commit SHAs and runs the exact local quality
gates documented in [`docs/development.md`](../../docs/development.md).

## Contents

- `ci.yml`: the required quality workflow. It runs on every push to `main` and
  on every pull request targeting `main`, and on pushes to any `codex/**`
  branch. It includes a separate Docker build and direct `--help` smoke test.
- `docs.yml`: the GitHub Pages workflow. It runs on pushes to `main` and on
  manual dispatch, builds the strict MkDocs Material site, and deploys it to
  the `github-pages` environment.

## What the CI does

The single `quality-gate` job, on `ubuntu-latest`:

1. Check out the repository with `actions/checkout`.
2. Install `uv` and Python 3.12 and enable caching with `astral-sh/setup-uv`.
3. Install `osmium-tool` via `apt-get` so the integration tests can run
   against the real extractor. The package update and install use IPv4,
   bounded network timeouts, and retries; the quality job has a 20-minute
   limit so a stalled package mirror cannot consume a runner indefinitely.
4. Install Just.
5. Run `just ci`, which executes the deterministic
   `baseline → ruff → ty → tests → acceptance → architecture → CRAP →
   mutation → smoke → diff-review` gauntlet, then all-files pre-commit checks
   and the strict documentation build. Its `baseline` stage checks the lockfile
   and installs the locked environment; its `smoke` stage tests the installed
   wheel and packaged resources.
6. Check the branch diff for whitespace errors.

The `docker-smoke` job separately builds the pinned production image and runs
its direct CLI entrypoint with `--help`. It does not mount PBFs or a data root
and does not receive Hugging Face credentials.

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
