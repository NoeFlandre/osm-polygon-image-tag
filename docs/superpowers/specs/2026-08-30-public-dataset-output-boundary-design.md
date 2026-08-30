# Public Dataset Output Boundary Design

## Goal

Separate final public-output assembly from polygon deduplication so the public
dataset materializer has one clear owner for source processing and checkpoint
reuse, while output serialization remains independently testable.

## Current problem

`artifacts/public_dataset.py` coordinates polygon accumulation and reuse, but it
also defines `PublicDatasetResult`, computes the release manifest payload, and
writes the public manifest. These responsibilities are coupled even though
output assembly only consumes finalized polygon/assets inputs and does not
need to know how polygon checkpoints are processed.

## Chosen design

Create `artifacts/public_dataset_output.py` containing the unchanged
`PublicDatasetResult`, `_manifest_payload`, and `_write_public_dataset`
implementations. Import those names from `public_dataset.py` so its existing
private import paths remain compatible, and keep `build_public_dataset` and
polygon materialization in `public_dataset.py`. No artifact paths, manifest
fields, hashing, row counts, serialization, or cleanup behavior changes.

## Compatibility contract

- Preserve `public_dataset.PublicDatasetResult`,
  `public_dataset._manifest_payload`, and
  `public_dataset._write_public_dataset` as aliases to the moved definitions.
- Preserve `PublicDatasetResult` fields, defaults, constructor order, and
  positional construction behavior.
- Preserve `build_public_dataset`, reuse behavior, manifest bytes, and public
  output paths unchanged.
- Do not add an output abstraction framework or alter the public dataset
  contract.

## Testing strategy

First add an ownership test that imports `public_dataset_output` before it
exists and asserts the moved definitions and compatibility aliases. Observe
the expected collection failure, then move the existing implementations with
the smallest import changes and run the public-dataset tests. After green,
update the artifact architecture documentation and run every repository gate.
