# Public Polygon Accumulator Boundary Design

## Context

`artifacts/public_dataset.py` currently combines the public release manifest,
Parquet validation, build orchestration, and the SQLite-backed polygon
accumulator. The accumulator owns durable row selection, source provenance,
checkpoint metadata, and payload restoration, so its implementation can be
understood and tested independently of release assembly. The file is more
than 1,000 lines even after the public image/link schema and checkpoint policy
were separated.

The repository's explicit complexity scan also reports the deterministic
checkpoint/publication race test above the configured C901 threshold. Its
behavior is valuable, but fixture construction and thread coordination make
the test body harder to review than the production behavior it protects.

## Chosen design

Create `artifacts/public_polygon_accumulator.py` as the focused owner of:

- `_PolygonAccumulator` and its SQLite schema, transactions, row ranking, and
  durable payload storage;
- polygon identity and rank helpers;
- source-provenance grouping and payload restoration;
- polygon checkpoint metadata/source validation and compatibility cleanup; and
- `PUBLIC_DEDUP_CHECKPOINT_SCHEMA_VERSION`.

Keep `public_dataset.py` responsible for the public polygon schema, release
manifest validation, reuse decisions, and build orchestration. It will import
the moved names so existing module-level access paths remain available, while
the production call graph uses the focused module directly. No SQL schema,
serialization protocol, ranking rule, source ordering, checkpoint metadata,
Parquet output, exception, or public function signature changes.

Refactor the race test into deterministic fixture and observation helpers.
The helpers will use the same `threading.Event` and `threading.Barrier`
protocol, preserve the existing timeout and failure assertions, and keep the
test's top-level narrative focused on the concurrency contract.

## Alternatives considered

1. Extract only polygon checkpoint metadata helpers. This leaves the large
   accumulator and its row/provenance logic coupled to release orchestration,
   so the improvement is too small.
2. Rebuild the accumulator around a new generic database abstraction. This
   would add an abstraction without a second concrete consumer and risks
   changing SQLite behavior.
3. Keep the accumulator in place and suppress C901. This hides the structural
   problem and makes the race test less maintainable.

The focused module extraction plus a local test decomposition is the smallest
design that addresses both observed quality issues without changing behavior.

## Verification

The implementation will use a red-green-refactor sequence. A new direct
module-boundary test will first fail because the focused module does not exist.
After the minimal extraction, the boundary and artifact tests must pass. The
race test refactor must then make `ruff check --select C901 .` pass without
weakening the test. The full repository contract remains `just qa`, followed
by pre-commit and strict documentation builds.
