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

install-hooks:
    uv run pre-commit install --hook-type pre-commit --hook-type pre-push

ci:
    just check
    uv run pre-commit run --all-files
    just test
    just build
    git diff --check
