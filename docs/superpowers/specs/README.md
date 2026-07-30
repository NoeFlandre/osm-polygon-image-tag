# `docs/superpowers/specs/`

Approved design specifications for the pipeline. Each spec captures the
intentional contract at a particular point in the project's evolution.

## Files

- `2026-07-29-osm-polygon-image-tag-design.md`: the original design
  specification. It defines the immutable source/data boundary, the layer
  responsibilities, the GeoParquet schema, the publication model, and the
  phase plan. The publication directory referenced as `publication/` in
  this document is now `receipts/` in the current contract.
- `2026-07-29-additional-image-tags-design.md`: the additive design that
  introduced `bubbleid`, indexed `panoramax:<n>` references, and the
  `panoramax_values` map. This is where the current schema v2 fields are
  documented; treat the additive spec as the authoritative reference for
  those columns.

## How to read

- Start with `data-contract.md` for the live contract.
- Read these specs only when you need historical rationale or when
  reconciling older assumptions.
