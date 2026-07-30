# `docs/superpowers/`

Historical design specifications and implementation plans that drove the
project through its phases. Treat them as archaeology rather than current
documentation.

## Layout

- `specs/`: design specifications, both the initial design and additive
  specifications such as the additional image tags design.
- `plans/`: phase-by-phase implementation plans used to deliver each
  feature in tight RED/GREEN slices.

## Reading the historical documents

- They may use the older `mypy` toolchain in their example commands. The
  current development workflow uses `ty` (see `docs/development.md`).
- They describe the package layout that existed at the time. The package has
  since been reorganized into `core`, `ingest`, `artifacts`, `runtime`, and
  `integrations` subpackages (see `docs/architecture.md`).
- The historical design spec mentions `publication/` as the receipts
  directory. The current contract stores receipts under `receipts/` inside
  the managed data root.

If a historical plan and a current-facing document disagree, the current
document wins.
