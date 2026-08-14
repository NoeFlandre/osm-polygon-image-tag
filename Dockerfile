# syntax=docker/dockerfile:1.7

# The digest is the multi-platform manifest for python:3.12.13-slim-bookworm
# as of 2026-08-14. Keep the digest and the Python minor line together.
ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

FROM ${PYTHON_IMAGE} AS build

# uv 0.11.16 is the version used by the repository CI lock/install contract.
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1

WORKDIR /app

# Keep dependency installation in a cacheable layer. The project itself is
# installed only after its source has been copied into the build context.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime

# Debian bookworm currently carries osmium-tool 1.15.0-1. This is the
# external CLI used by the pipeline; the locked Python `osmium` dependency is
# pyosmium and does not provide this executable.
ARG OSMIUM_TOOL_VERSION=1.15.0-1
RUN apt-get update \
    && apt-get install --no-install-recommends --yes \
        ca-certificates \
        "osmium-tool=${OSMIUM_TOOL_VERSION}" \
    && test "$(osmium --version | awk 'NR == 1 { print $3 }')" = "${OSMIUM_TOOL_VERSION%%-*}" \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    HOME=/tmp \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Keep the CLI as PID 1 so Python receives SIGINT/SIGTERM directly. Mount a
# read-only source tree at /raw and a persistent writable data root at /data.
ENTRYPOINT ["osm-polygon-image-tag"]
CMD ["--help"]
