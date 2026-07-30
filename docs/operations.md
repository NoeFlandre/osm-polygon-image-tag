# Operations

This document explains the operator-facing commands, the layout of the
managed data root, the resumability model, the progress events emitted to
stderr, and how the pipeline responds to control signals.

## Prerequisites

- `uv` for environment and dependency management.
- The `osmium` executable on `PATH`. Install it from your package manager
  (for example, `brew install osmium-tool` on macOS or `apt install osmium-tool`
  on Debian/Ubuntu).
- A read-only Geofabrik `.osm.pbf` tree and a writable data root on a
  filesystem with enough free space.

## Real-world path examples (not portable defaults)

These are the paths used by the live production pipeline on this machine.
They are concrete examples, not portable defaults. Substitute your own
paths when running locally.

- Read-only PBF source root:
  `/Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw`
- Managed data root:
  `/Volumes/Seagate M3/projects/osm-polygon-image-tag`

When you are not running against the live production data, prefer small
throwaway paths under your home directory.

## Managed data-root layout

Everything the pipeline writes is contained inside the data root, under
fixed namespaces:

```
data-root/
  data/                # one GeoParquet shard per source PBF
  manifests/           # one manifest per shard
  statistics/          # dataset-statistics.json (deterministic)
  catalog/             # catalog.sqlite (rebuildable index)
  receipts/            # publication.json after a successful publish
  tmp/                 # incomplete project-owned artifacts
  README.md            # generated Hugging Face dataset card
```

Symlinks, special files, or unexpected entries fail closed during
publication planning. The pipeline never deletes files it does not own.

## Commands

- `preflight`: validates that the source root is readable, `osmium` is
  available, the data root has enough capacity, and reports the discovered
  PBF inventory. Mutates nothing.
- `run`: processes or resumes every PBF locally. Each completed PBF
  produces a deterministic GeoParquet shard and a manifest. Skipped PBFs
  reuse verified shards without rehashing the source or output.
- `verify`: revalidates every finalized shard by recomputing its SHA-256
  and structurally re-reading the Parquet file. This is the deep
  verification path; the fast resume used by `run` and `run-and-publish`
  is cheaper.
- `rebuild-metadata`: rebuilds the catalog, statistics, and dataset card
  from the existing shards without rebuilding or publishing anything.
- `publish`: publishes only the existing verified artifacts to the
  configured Hugging Face dataset.
- `run-and-publish`: processes one PBF, regenerates metadata, and
  publishes. The loop continues until every PBF is processed or until the
  operator stops it.

## Fast resume versus explicit deep verification

`run` and `run-and-publish` use the fast resume path. For each PBF they
read the manifest's recorded source size and mtime, confirm the output
file exists at the expected size, and confirm the current processing and
schema versions match. If all of those match, the shard is reused without
rehashing the PBF or re-reading the Parquet file.

`verify` is the explicit deep verification path. It recomputes the source
SHA-256, recomputes the output SHA-256, and re-reads the Parquet structure
to confirm the row count and schema. Use it after a suspected corruption
event or before publishing for the first time after a long pause.

## Skipped PBFs do not regenerate or publish metadata

`run-and-publish` only runs `generate_metadata` and the publisher after a
shard is newly built. A resume that only skips previously verified PBFs
does not regenerate the dataset card, does not refresh the catalog, and
does not commit to Hugging Face. The next `rebuild-metadata` or `publish`
command will run only when the operator chooses to.

## Progress events and heartbeats

Every long-running command emits JSON events to stderr as
`progress {"event":"...", ...}`. The events include:

- `run_started`, `pbf_started`, `pbf_completed`, `run_completed` for the
  orchestrator.
- `metadata_manifest_scan_started`, `metadata_manifest_scan_progress`,
  `metadata_manifest_scan_completed` for the catalog scan.
- `metadata_catalog_sync_started`, `metadata_catalog_shard_started`,
  `metadata_catalog_shard_completed`, `metadata_catalog_sync_completed`
  for the per-shard catalog sync.
- `metadata_statistics_started`, `metadata_statistics_completed`,
  `metadata_write_started`, `metadata_write_completed`.
- `publication_started`, `publication_completed`.
- `heartbeat` events emitted every 30 seconds with the last event name
  and elapsed seconds. They keep long-running runs observable without
  flooding the log.

The exact, final report is always printed to stdout as one JSON object.

## Control signals

`SIGINT` (Ctrl-C) and `SIGTERM` set the orchestrator's stop token. It does not
start another PBF after the current build returns. A terminal signal can also
reach the active `osmium` subprocess; if that aborts extraction, the current
shard fails safely instead of being promoted. Already finalized shards remain
valid because promotion uses atomic rename.

## Safe Ctrl-C behaviour

- During extraction: the current build either returns normally or fails
  without promoting a partial shard; no next PBF is started after the token
  is observed.
- During metadata or publication: signal delivery and the remote SDK determine
  whether the in-flight operation returns or raises; finalized prior shards
  and receipts remain valid.
- After a hard kill: stale temporary files are confined to `tmp/` (for
  the per-PBF tag-store) and to the partially-written `.tmp` sibling of
  any Parquet or manifest write. Publication preserves and rejects all of
  these files because, without a process lock, they may belong to an active
  run. Resume the pipeline so its owning operation can clean up safely.

## When to use which command

- First time: `preflight`, then `run`, then `verify`, then `publish`.
- Resuming after a stop or crash: `run` or `run-and-publish`.
- After a suspected corruption: `verify`.
- After a schema change: `run` (it will rebuild under the new contract).
- To refresh the dataset card without touching PBFs: `rebuild-metadata`.
