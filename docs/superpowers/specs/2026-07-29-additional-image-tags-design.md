# Additional Image Tags Design

## Goal

Include Bing Streetside `bubbleid=*` references and all indexed Panoramax
references named `panoramax:<non-negative integer>=*`, while preserving the
existing raw, resumable, per-PBF dataset contract.

## Matching contract

An area feature is selected when it has at least one non-empty value for:

- `image`
- `wikimedia_commons`
- `mapillary`
- `panoramax`
- `panoramax:<n>`, where `<n>` contains one or more ASCII digits
- `kartaview`
- `flickr`
- `bubbleid`

Keys such as `panoramax:left`, `panoramax:`, and `panoramax:1:foo` do not match
the indexed-key contract. Original tags remain preserved losslessly even when
they do not match.

## Dataset schema

Keep the existing nullable scalar `panoramax` column for compatibility. Add:

- nullable UTF-8 `bubbleid`
- non-null `panoramax_values` map of UTF-8 key to UTF-8 value

`panoramax_values` contains the exact `panoramax` entry and every matching
indexed entry, sorted by key. It is empty when none exist. Keeping the original
keys in the map preserves indices without an unbounded wide schema.

All original OSM tags continue to be stored in `tags`.

## Resumption and compatibility

Increment the dataset schema and processing-contract versions. Existing
manifests therefore cannot be reused, so every previously completed PBF is
rebuilt under the new schema. Writes remain atomic; interruption leaves the
previous verified shard reusable only by the old contract, and the next new
run rebuilds it.

The currently running process uses the old code and schema. The operator may
press Ctrl-C once at any time; it completes metadata/publication for the current
PBF and stops before starting another. The new command must not be started
until the implementation is merged.

## Reporting

Dataset statistics and the generated card add `bubbleid` counts. Panoramax
provider counts treat an observation as Panoramax-backed when either the exact
or any indexed Panoramax key is present, counting each observation once.

## Testing

RED→GREEN tests cover:

- indexed Panoramax selection and rejected lookalike keys
- `bubbleid` selection
- deterministic transformation into `bubbleid` and `panoramax_values`
- Arrow/GeoParquet schema validation
- reporting counts across exact and indexed Panoramax references
- old-manifest invalidation through bumped contract versions
- real local fixture extraction through GeoParquet

The full dependency, test, lint, formatting, strict typing, and diff gates must
pass before merge.
