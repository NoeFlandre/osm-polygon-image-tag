# `ingest/`

Read-only ingestion of Geofabrik `.osm.pbf` files. Everything in this package
treats the raw PBF tree as immutable input and never writes back to it.

## What belongs here

- Deterministic, depth-first PBF discovery (`discover_pbfs`).
- Streaming `osmium export` invocation (`osmium`) and COPY-record parsing
  (`copy_parser`).
- Exact target-key selection and source-tag restoration (`tag_policy`).
- The temporary SQLite tag-store used to restore original tag values during
  extraction.
- Row-level transformation that applies the geometry, area, and tag rules.

## What must not belong here

- Anything that writes to the managed data root.
- Anything that talks to Hugging Face or any other remote service.
- Anything that knows about the dataset card or publication inventory.

## Public entry points and contracts

- `discover_pbfs(source_root) -> tuple[PbfSource, ...]`: rejects symlinks,
  special files, and non-regular entries.
- `stream_export(pbf_path, config_path, executable=...)`: bounded `osmium`
  subprocess with stderr retention.
- `transform_record(record, source_pbf=...) -> AcceptedRow | RejectedRow`:
  the canonical row-decision function.
- `is_target_tag_key`, `has_target_tag`, `panoramax_tag_values`: tag
  selection helpers shared with reporting.

## Dependencies

- `core` for `ImageTagPipelineError`, schema, manifest.
- `pyarrow`, `pyproj`, `shapely`; the `osmium` command is a runtime tool.
- The package-data resource `_data/osmium-export.json`.

## Focused tests

```bash
uv run pytest tests/unit/ingest -q --no-cov
```
