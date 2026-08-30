# `runtime/`

Composition of every other layer into resumable, stoppable, observable
workflows. Modules here are the only place where ingest, artifacts, and
integrations are wired together.

## What belongs here

- The per-PBF build pipeline (`pipeline`) that owns fast resume and explicit
  deep verification.
- The full-run orchestrator (`orchestrator`) that drives `run`,
  `run-and-publish`, and `verify`, including `SIGINT`/`SIGTERM` handling.
- The immutable run and verification result contracts (`results`) shared by
  the orchestrator and CLI.
- The bounded background enrichment worker (`enrichment`) and TTY/JSON
  rendering boundary (`console`).
- The read-only preflight (`preflight`) used by `osm-polygon-image-tag
  preflight`.
- Startup cleanup of abandoned, application-owned temporary files from a
  prior stopped run (`cleanup`). Unknown files are never removed.
- Package-data resource resolution (`resources`).

## What must not belong here

- Direct imports of `huggingface_hub`. The Hugging Face adapter lives in
  `integrations.huggingface`; provider-neutral publication types live in
  `artifacts`.
- Hard-coded remote calls. Real publication is invoked by the CLI and is
  reviewed before each deployment.

## Public entry points and contracts

- `PipelinePaths` is built in `core.config` and consumed unchanged here.
- `run_all`, `verify_all`, `StopToken`, `graceful_stop_signals`:
  the orchestrator surface that the CLI wraps.
- `RunSummary`, `VerifySummary`: immutable result contracts from `results`,
  also re-exported by `orchestrator` for compatibility.
- `build_one`, `verify_one`: the per-PBF entry points used by integration
  tests and the orchestrator.
- `run_preflight`, `probe_osmium`, `probe_capacity`: the read-only checks.

## Dependencies

- Every other subpackage.
- `pyarrow`, `pyproj`, and `shapely` through the composed lower layers.

## Focused tests

```bash
uv run pytest tests/unit/runtime -q --no-cov
```
