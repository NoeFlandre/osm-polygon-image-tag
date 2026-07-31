# `docs/`

Project documentation outside the package and tests. MkDocs Material builds
the current-facing pages from this folder and publishes them through GitHub
Pages at <https://noeflandre.github.io/osm-polygon-image-tag/>.

## Layout

- `architecture.md`: the layered structure of the package and the rules
  that govern how layers talk to each other.
- `index.md`: the public landing page.
- `getting-started.md`: prerequisites and a first safe run.
- `cli.md`: the exact command and option reference.
- `data-contract.md`: the GeoParquet schema, manifest contract, processing
  contract, and exactly which OSM tags are selected.
- `operations.md`: how to run the pipeline locally, manage the data root,
  observe progress, handle Ctrl-C, and reason about resumability.
- `development.md`: how to set up the development environment, run the test
  suite, lint, type-check, build the wheel, and contribute changes.

## How this folder is maintained

- Current-facing documentation (`architecture.md`, `data-contract.md`,
  `operations.md`, `development.md`, `index.md`, `getting-started.md`, and
  `cli.md`) is authoritative and is built by `mkdocs.yml`.
- Agent plans and design notes are intentionally kept out of the public
  repository and MkDocs site. Keep any private working copies under the
  ignored `docs/superpowers/` path if they are needed locally.
