# CLI reference

The installed command is `osm-polygon-image-tag`. Every command accepts a
source PBF root, a managed data root, and an optional `--log-format` of
`auto`, `json`, or `human`.

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

Rebuilds the catalog, deterministic statistics, and dataset card from existing
finalized artifacts. It does not reopen PBF files.

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
