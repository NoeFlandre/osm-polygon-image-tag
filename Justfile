set shell := ["bash", "-euo", "pipefail", "-c"]

sync:
    uv sync --locked --dev

unit:
    uv run pytest tests/unit -q --no-cov

integration:
    uv run pytest tests/integration -q --no-cov

test:
    uv run pytest -q

check:
    uv lock --check
    uv run ruff check .
    uv run ruff format --check .
    uv run ty check

build:
    uv build

docs:
    uv run mkdocs build --strict --site-dir site

mutation:
    uv run mutmut run --max-children 2
    uv run mutmut results

crap:
    uv run pytest -q
    uv run coverage lcov -o /tmp/osm-polygon-image-tag-coverage.info
    uv run crap4py src --lcov /tmp/osm-polygon-image-tag-coverage.info --max-crap 5.99 --max-workers 2

quality-advanced:
    just mutation
    just crap

install-hooks:
    uv run pre-commit install --hook-type pre-commit --hook-type pre-push

ci:
    just check
    uv run pre-commit run --all-files
    just test
    just build
    just docs
    git diff --check
