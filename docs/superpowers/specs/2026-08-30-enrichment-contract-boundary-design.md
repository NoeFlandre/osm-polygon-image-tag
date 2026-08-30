# Enrichment Contract Boundary Design

## Goal

Separate the immutable enrichment data contracts from the concurrent worker
implementation so result and orchestration code can depend on small, focused
types.

## Current problem

`runtime/enrichment.py` owns both `AssetJob`/`EnrichmentSummary` and the
threaded/asynchronous `EnrichmentWorker`. `runtime/results.py` therefore
imports the worker module merely to use `EnrichmentSummary`, pulling queue,
asyncio, asset-builder, and cache wiring into a simple result-contract import.
This obscures ownership and makes the contracts harder to reuse and test in
isolation.

## Chosen design

Create `runtime/enrichment_types.py` containing the unchanged `AssetJob` and
`EnrichmentSummary` definitions. Keep compatibility imports in
`runtime/enrichment.py`, update orchestration to import the types directly, and
make `runtime/results.py` depend on the focused types module. No worker logic,
constructor order, fields, defaults, status behavior, or CLI output changes.

## Compatibility contract

- Preserve `runtime.enrichment.AssetJob` and
  `runtime.enrichment.EnrichmentSummary` as aliases to the moved classes.
- Preserve all fields, defaults, constructor order, and
  `EnrichmentSummary.status_counts()` behavior.
- Preserve `EnrichmentWorker`, `run_all`, `verify_all`, and CLI dependency
  injection signatures unchanged.
- Do not introduce a generic contract framework or alter concurrency logic.

## Testing strategy

First add an ownership and compatibility test that imports
`runtime.enrichment_types` before the module exists and asserts the moved
classes and old import aliases. Observe the expected collection failure, then
add the smallest module/import changes and run the runtime tests. After green,
update ownership documentation and run every repository quality gate.
