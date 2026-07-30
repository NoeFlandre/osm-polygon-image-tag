# `_data/`

Package data shipped with the installed wheel. These files are referenced by
`importlib.resources` from inside the package so the same configuration works
whether the project is invoked from a `uv` checkout, an editable install, or
a published wheel.

## Contents

- `osmium-export.json`: the policy file passed to `osmium export`. It keeps
  every OSM attribute we care about (type, id, version, changeset,
  timestamp) while preserving every original tag as a JSON object on each
  feature.

## What belongs here

- Static configuration consumed by third-party executables.
- Reference tables or schemas that the runtime needs verbatim.

## What must not belong here

- Generated artifacts, caches, or anything mutated at runtime. Those belong
  inside the managed data root.
- Secrets, credentials, or anything operator-specific.

## Dependencies

- Read at runtime by `osm_polygon_image_tag.runtime.resources` and the
  extraction layer in `osm_polygon_image_tag.ingest.extraction`.

## Focused tests

```bash
uv run pytest tests/unit/ingest -q --no-cov
```
