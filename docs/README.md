# `docs/`

Project documentation outside the package and tests. This folder explains the
pipeline at the architecture, data contract, operations, and developer levels.

## Layout

- `architecture.md`: the layered structure of the package and the rules
  that govern how layers talk to each other.
- `data-contract.md`: the GeoParquet schema, manifest contract, processing
  contract, and exactly which OSM tags are selected.
- `operations.md`: how to run the pipeline locally, manage the data root,
  observe progress, handle Ctrl-C, and reason about resumability.
- `development.md`: how to set up the development environment, run the test
  suite, lint, type-check, build the wheel, and contribute changes.
- `superpowers/specs/`: historical and additive design specifications.
- `superpowers/plans/`: historical implementation plans used during initial
  development. They document how the project was built, not the current
  contract; see `data-contract.md` and `architecture.md` for the live view.

## How this folder is maintained

- Current-facing documentation (`architecture.md`, `data-contract.md`,
  `operations.md`, `development.md`) is authoritative.
- Historical plans and specs are preserved untouched so they continue to
  describe the work they originally motivated. If a historical document
  contradicts a current-facing one, treat the current-facing document as
  the source of truth.
