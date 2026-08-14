# CLI reference

The installed command is `osm-polygon-image-tag`. Every command accepts a
source PBF root, a managed data root, and an optional `--log-format` of
`auto`, `json`, or `human`.

## Shared options

These options have the same meaning on every command:

| Option | Meaning |
| --- | --- |
| `--source-root PATH` | Directory containing the read-only Geofabrik `.osm.pbf` files. |
| `--data-root PATH` | Writable directory for resumable shards, caches, metadata, and receipts. |
| `--log-format auto\|json\|human` | `auto` uses a readable TTY display; `json` emits machine-readable progress; `human` forces the readable display. |

Both paths are required. The source root must be a real directory, and the
source and data roots may not overlap. Publication also rejects symlinks or
unexpected entries inside the managed data root. `--confirm-repo` is required
by `publish` and `run-and-publish` and must be exactly
`NoeFlandre/osm-polygon-image-tag`.

## Choose a command

| Goal | Command |
| --- | --- |
| Check paths and prerequisites without writing | `preflight` |
| Build or resume local artifacts without publishing | `run` |
| Recheck every finalized artifact deeply | `verify` |
| Rebuild the card, map, catalog, and statistics without PBF extraction | `rebuild-metadata` |
| Publish the existing verified release | `publish` |
| Build/resume and publish changed verified artifacts | `run-and-publish` |

## `preflight`

```bash
osm-polygon-image-tag preflight \
  --source-root PATH --data-root PATH [--log-format FORMAT]
```

Checks paths, capacity, `osmium`, and the discovered PBF inventory without
writing output.

## `run`

```bash
osm-polygon-image-tag run \
  --source-root PATH --data-root PATH [--log-format FORMAT]
```

Extracts or resumes polygon shards and backfills missing asset shards. Resume
uses manifest identity and contract versions for a fast skip.

## `verify`

```bash
osm-polygon-image-tag verify \
  --source-root PATH --data-root PATH [--log-format FORMAT]
```

Performs deep SHA-256, row-count, schema, and source/output validation.

## `rebuild-metadata`

```bash
osm-polygon-image-tag rebuild-metadata \
  --source-root PATH --data-root PATH [--log-format FORMAT]
```

Rebuilds the catalog, deterministic statistics, map, and dataset card from
existing finalized artifacts. It does not reopen PBF files. The
`--source-root` argument is still required for the common path-safety check,
but the command does not read that directory.

## `publish`

```bash
osm-polygon-image-tag publish \
  --source-root PATH --data-root PATH \
  --confirm-repo NoeFlandre/osm-polygon-image-tag \
  [--log-format FORMAT]
```

Publishes existing verified artifacts to the Hugging Face dataset. The
confirmation must exactly match the configured repository.

## `run-and-publish`

```bash
osm-polygon-image-tag run-and-publish \
  --source-root PATH --data-root PATH \
  --confirm-repo NoeFlandre/osm-polygon-image-tag \
  [--log-format FORMAT]
```

Runs extraction, historical asset enrichment, metadata generation, and guarded
publication. This is the normal production and resume command.

## Output and logging

The final summary is one JSON object on stdout. Long-running progress events
and heartbeats go to stderr. Use `--log-format json` for automation; TTY
`auto` mode adds Rich/tqdm rendering without changing the final summary.
