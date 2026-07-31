# Repository automation

This folder contains the repository's CI and documentation-publishing
configuration. It describes how changes are checked and how the public MkDocs
site is deployed; the dataset pipeline itself lives under `src/`.

## Workflows

- `workflows/ci.yml` runs the locked quality gate on pushes to `main`, pushes to
  `codex/**`, and pull requests: Ruff, formatting, ty, pytest, packaging, and
  whitespace checks.
- `workflows/docs.yml` builds the strict MkDocs Material site and deploys it to
  [GitHub Pages](https://noeflandre.github.io/osm-polygon-image-tag/) on pushes
  to `main` or a manual dispatch.

Both workflows use the repository's locked `uv` environment. Third-party
actions are pinned to immutable commit SHAs; update a SHA and its version
comment together.

## Safety boundary

The workflows are verification and documentation jobs, not production runs.
They do not read the Seagate PBF source tree, access a local data root, publish
to the Hugging Face dataset, or require Hugging Face, Mapillary, or Flickr
credentials. Production processing remains an explicit local CLI operation;
see [Operations and credentials](../docs/operations.md).

## Run the same checks locally

```bash
uv sync --locked --dev
just ci
just docs
```

The root [README](../README.md) explains the dataset and resumable pipeline.
The workflow-specific details are in [`workflows/README.md`](workflows/README.md).
