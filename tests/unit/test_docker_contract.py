"""Static contract checks for the production Docker image."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"


def test_dockerfile_pins_runtime_and_toolchain_contract() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert re.search(
        r"^ARG PYTHON_IMAGE=python:3\.12\.\d+-slim-bookworm@sha256:[0-9a-f]{64}$",
        dockerfile,
        re.MULTILINE,
    )
    assert "FROM ${PYTHON_IMAGE} AS build" in dockerfile
    assert "FROM ${PYTHON_IMAGE} AS runtime" in dockerfile
    assert "ghcr.io/astral-sh/uv:0.11.16" in dockerfile
    assert "uv sync --locked --no-dev --no-install-project" in dockerfile
    assert "uv sync --locked --no-dev --no-editable" in dockerfile
    assert "ARG OSMIUM_TOOL_VERSION=1.15.0-1" in dockerfile
    assert '"osmium-tool=${OSMIUM_TOOL_VERSION}"' in dockerfile
    assert 'ENTRYPOINT ["osm-polygon-image-tag"]' in dockerfile
    assert 'CMD ["--help"]' in dockerfile
    assert 'ENV PATH="/app/.venv/bin:${PATH}"' in dockerfile
    assert "MPLCONFIGDIR=/tmp/matplotlib" in dockerfile


def test_dockerignore_excludes_data_credentials_and_build_outputs() -> None:
    dockerignore = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()

    for entry in (
        ".git",
        ".venv",
        "*.osm.pbf",
        "*.parquet",
        "*.sqlite",
        "*.env",
        "data/",
        "tests/",
    ):
        assert entry in dockerignore


def test_dockerfile_does_not_copy_runtime_secrets_or_data() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "HF_TOKEN" not in dockerfile
    assert "MAPILLARY_ACCESS_TOKEN" not in dockerfile
    assert "FLICKR_API_KEY" not in dockerfile
    assert "COPY ." not in dockerfile


def test_ci_builds_and_smoke_tests_image_without_pipeline_inputs() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "  docker-smoke:" in workflow
    docker_job = workflow.split("  docker-smoke:", maxsplit=1)[1]
    assert "docker build --tag osm-polygon-image-tag:ci ." in docker_job
    assert "docker run --rm --read-only --tmpfs /tmp osm-polygon-image-tag:ci --help" in docker_job
    assert "HF_TOKEN" not in docker_job
    assert "*.osm.pbf" not in docker_job


def test_ci_bounds_osmium_installation() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "timeout-minutes: 20" in workflow
    assert "Acquire::Retries=3" in workflow
    assert "Acquire::ForceIPv4=true" in workflow
    assert "timeout --kill-after=30s 5m sudo apt-get" in workflow
    assert "osmium --version" in workflow
