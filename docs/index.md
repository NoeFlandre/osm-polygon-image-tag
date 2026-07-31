# OSM Polygon Image Tag

`osm-polygon-image-tag` builds a reproducible GeoParquet dataset of OpenStreetMap
ways and relations that carry image references. It reads Geofabrik PBF files
without modifying them, preserves the complete source tags, and enriches the
result with directly usable image or page URLs when a provider permits it.

The pipeline is designed for long-running local jobs:

- extraction and asset enrichment are resumable and safe to stop;
- finalized shards are reused instead of reopening completed PBFs;
- metadata and Hugging Face publication are deterministic and receipt-aware;
- provider credentials stay in the environment and never enter durable cache
  or dataset metadata.

## Start here

1. Install the prerequisites in [Getting started](getting-started.md).
2. Run `preflight` against a read-only PBF tree and writable data root.
3. Start or resume with `run-and-publish`.
4. Use [Operations](operations.md) for credentials, progress, and safe stops.

## Documentation map

- [CLI reference](cli.md) — every command and option.
- [Architecture](architecture.md) — package boundaries and data flow.
- [Data contract](data-contract.md) — selected tags, schemas, statuses, and
  cache/privacy rules.
- [Operations](operations.md) — data-root layout, resume behavior, credentials,
  and control signals.
- [Development](development.md) — `uv`, Ruff, ty, pytest, pre-commit, Just,
  and CI workflows.

The generated dataset is published at
<https://huggingface.co/datasets/NoeFlandre/osm-polygon-image-tag>.
