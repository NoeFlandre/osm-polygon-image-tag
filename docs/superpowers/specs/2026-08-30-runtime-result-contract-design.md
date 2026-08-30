# Runtime Result Contract Design

## Goal

Separate immutable CLI result contracts from workflow coordination so runtime
orchestration has one clear owner for execution flow.

## Current problem

`runtime/orchestrator.py` coordinates source builds, enrichment checkpoints,
publication, and verification, but it also defines `RunSummary` and
`VerifySummary`, the small data contracts consumed by the CLI and tests. This
mix makes the result representation depend on the orchestration module even
though the contracts are useful independently of execution.

## Chosen design

Create `runtime/results.py` containing the unchanged `RunSummary` and
`VerifySummary` dataclasses and their `to_dict` methods. Import those names in
`runtime/orchestrator.py` so the existing import path remains compatible, and
make `cli.py` depend on the focused result module directly. No orchestration
logic, serialization, field order, defaults, or output behavior changes.

## Compatibility contract

- Preserve all fields, defaults, constructor order, `to_dict()` output, and
  exception behavior.
- Preserve `runtime.orchestrator.RunSummary` and
  `runtime.orchestrator.VerifySummary` as aliases to the moved classes.
- Keep `run_all`, `verify_all`, signal handling, and CLI dependency injection
  signatures unchanged.
- Do not add a generic result framework or change runtime behavior.

## Testing strategy

First add a direct ownership test that imports `runtime.results` before the
module exists and asserts the compatibility aliases. Observe the expected
collection failure, then add the smallest module/import changes and run the
runtime/CLI tests. After green, update the runtime documentation and run every
repository quality gate.
