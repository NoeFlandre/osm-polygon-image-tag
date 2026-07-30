# `docs/superpowers/plans/`

Phase-by-phase implementation plans used during initial development. They are
preserved as the historical record of how the project was built.

## Files

- `2026-07-29-phase-1-project-foundation.md`: Phase 1 implementation plan.
- `2026-07-29-phase-2-extraction-contract.md`: Phase 2.
- `2026-07-29-phase-3-geoparquet-storage.md`: Phase 3.
- `2026-07-29-phase-4-resumable-shards.md`: Phase 4.
- `2026-07-29-phase-5-reporting.md`: Phase 5.
- `2026-07-29-phase-6-guarded-publication.md`: Phase 6.
- `2026-07-29-additional-image-tags.md`: the additive plan that delivered
  indexed Panoramax and Bing `bubbleid` support.
- `2026-07-30-direct-image-assets.md`: the approved implementation plan for
  cached provider resolution, one-to-many asset shards, automatic historical
  backfill, two Hugging Face configurations, and the modern project toolchain.
  Implemented on `main`; current contracts live in the top-level documentation.

## Reading the plans

- The plans predate the migration to `ty`. Their example commands use
  `mypy`; the current development workflow uses `ty` (see
  `docs/development.md`). Do not rewrite the plans to use `ty`; treat them
  as historical.
- The plans describe the flat package layout that existed at the time. The
  package has since been split into `core`, `ingest`, `artifacts`,
  `runtime`, and `integrations` subpackages (see `docs/architecture.md`).
- Use the plans to understand the original RED/GREEN intent and the
  rationale behind the implementation; use the live documentation to learn
  the current contract.
