set shell := ["bash", "-euo", "pipefail", "-c"]

sync:
    uv sync --locked --dev

baseline:
    uv lock --check
    uv sync --locked --dev

ruff:
    uv run ruff check .
    uv run ruff format --check .

ty:
    uv run ty check

unit:
    uv run pytest tests/unit -q --no-cov

integration:
    uv run pytest tests/integration -q --no-cov

test: tests

tests:
    uv run pytest -q

acceptance: integration

architecture:
    uv run pytest tests/unit/core/test_architecture.py -q --no-cov

build:
    uv build

docs:
    uv run mkdocs build --strict --site-dir site

mutation:
    uv run python scripts/run_mutmut.py run --max-children 2
    uv run mutmut results --all=true | tee /tmp/osm-polygon-image-tag-mutmut.txt
    awk 'NF && $NF != "killed" { print; failed=1 } END { exit failed }' /tmp/osm-polygon-image-tag-mutmut.txt

crap-report:
    uv run coverage lcov -o /tmp/osm-polygon-image-tag-coverage.info
    uv run crap4py src --lcov /tmp/osm-polygon-image-tag-coverage.info --max-crap 5.99 --max-workers 2

crap:
    just tests
    just crap-report

quality-advanced:
    just mutation
    just crap

install-hooks:
    uv run pre-commit install --hook-type pre-commit --hook-type pre-push

smoke:
    uv build
    uv venv --clear /tmp/osm-polygon-image-tag-qa-wheel
    uv pip install --python /tmp/osm-polygon-image-tag-qa-wheel/bin/python dist/*.whl
    /tmp/osm-polygon-image-tag-qa-wheel/bin/osm-polygon-image-tag --help
    /tmp/osm-polygon-image-tag-qa-wheel/bin/python -c "from importlib.resources import files; import osm_polygon_image_tag as p; assert p.__version__ == '0.1.0'; root = files('osm_polygon_image_tag'); assert root.joinpath('_data/osmium-export.json').read_text(); assert root.joinpath('_data/hero.png').read_bytes().startswith(b'\\x89PNG\\r\\n\\x1a\\n'); assert root.joinpath('assets/README.md').read_text(); assert root.joinpath('resolvers/README.md').read_text()"

diff-review:
    git diff --check
    git diff --cached --check
    git diff-tree --check --no-commit-id -r HEAD

qa:
    just baseline
    just ruff
    just ty
    just tests
    just acceptance
    just architecture
    just crap-report
    just mutation
    just smoke
    just diff-review

check: baseline ruff ty

ci:
    just qa
    uv run pre-commit run --all-files
    just docs
