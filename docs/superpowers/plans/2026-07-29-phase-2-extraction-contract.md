# Phase 2 OSM Area Extraction Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream only standard OSM area ways and area relations from PBF input, preserve all tags and exact image-reference values, and prove the contract through a real-`osmium` synthetic fixture.

**Architecture:** A packaged, versioned `osmium export` configuration selects polygon geometry only and emits PostgreSQL COPY records. A bounded subprocess adapter parses immutable `ExportRecord` values without shell execution, and a pure target-tag predicate keeps only objects whose own tag map contains one of the six approved keys.

**Tech Stack:** Python 3.12 standard library, osmium-tool PostgreSQL COPY export, pytest, Ruff, and mypy.

---

## Scope and Stop Condition

This plan adds extraction only. It does not add Shapely transformation,
GeoParquet, manifests, resumability, statistics, CLI production commands, or
publication. Stop after synthetic real-`osmium` evidence and all local gates
pass.

### Task 1: Version the osmium Area Policy

**Files:**
- Create: `src/osm_polygon_image_tag/_data/osmium-export.json`
- Create: `src/osm_polygon_image_tag/resources.py`
- Modify: `pyproject.toml`
- Test: `tests/test_extraction_policy.py`

- [ ] Write a failing test asserting the packaged policy records OSM type, ID,
  version, changeset, timestamp, disables user/UID and way nodes, emits JSON
  tags, sets both `linear_tags` and `area_tags` to `true`, and has no include or
  exclude tag projection.
- [ ] Run `uv run pytest tests/test_extraction_policy.py -q --no-cov` and
  observe import failure for `resources`.
- [ ] Add package-data inclusion to Hatch and implement
  `osmium_export_config() -> Path` with `importlib.resources.as_file`.
- [ ] Add the exact JSON policy:

```json
{
  "attributes": {
    "type": "__osm_type",
    "id": "__osm_id",
    "version": "__osm_version",
    "changeset": "__osm_changeset",
    "timestamp": "__osm_timestamp",
    "uid": false,
    "user": false,
    "way_nodes": false
  },
  "format_options": {"tags_type": "json"},
  "linear_tags": true,
  "area_tags": true,
  "exclude_tags": [],
  "include_tags": []
}
```

- [ ] Run the focused test and commit
  `feat: version standard OSM area extraction policy`.

### Task 2: Parse Immutable Export Records and Match Target Tags

**Files:**
- Create: `src/osm_polygon_image_tag/extraction.py`
- Modify: `src/osm_polygon_image_tag/errors.py`
- Test: `tests/test_extraction.py`

- [ ] Write failing tests for the fixed argv:

```python
assert export_command(Path("/input/a.osm.pbf"), Path("/config.json")) == (
    "osmium", "export", "/input/a.osm.pbf",
    "--output-format", "pg",
    "--config", "/config.json",
    "--geometry-types", "polygon",
    "--output", "-",
)
```

- [ ] Add failing parser tests for seven COPY fields, PostgreSQL `\N`, double
  escaping in JSON tag values, malformed field count, malformed JSON, blank
  lines, and frozen records.
- [ ] Add failing predicate tests proving that each of `image`,
  `wikimedia_commons`, `mapillary`, `panoramax`, `kartaview`, and `flickr`
  matches by key presence even for an empty string, while similarly named keys
  do not match.
- [ ] Run the test and observe missing extraction symbols.
- [ ] Implement only:

```python
TARGET_TAG_KEYS = (
    "image", "wikimedia_commons", "mapillary",
    "panoramax", "kartaview", "flickr",
)

@dataclass(frozen=True, slots=True)
class ExportRecord:
    geometry_ewkb_hex: str
    osm_type: str
    osm_id: int
    version: int | None
    changeset: int | None
    timestamp: str | None
    tags: dict[str, str]

def has_target_tag(tags: Mapping[str, str]) -> bool:
    return any(key in tags for key in TARGET_TAG_KEYS)
```

  plus fixed-argv construction, COPY unescaping, JSON validation, and
  line-numbered iteration.
- [ ] Run focused and regression tests and commit
  `feat: parse and filter osmium area records`.

### Task 3: Stream osmium Safely and Bound Diagnostics

**Files:**
- Modify: `src/osm_polygon_image_tag/extraction.py`
- Test: `tests/test_extraction_stream.py`

- [ ] Write failing fake-executable tests for successful ordered streaming,
  missing executable, non-zero exit, bounded retained stderr, version
  detection, and consumer cancellation that terminates the child.
- [ ] Run focused tests and observe missing stream APIs.
- [ ] Implement `STDERR_CAP_BYTES = 64 * 1024`, typed `OsmiumExportError`,
  a fixed-argv `subprocess.Popen` call with no shell, a bounded stderr drain,
  generator-finalization termination/kill, and a ten-second version probe.
- [ ] Run focused and full tests and commit
  `feat: stream osmium export with bounded diagnostics`.

### Task 4: Prove Closed-Way and Relation Semantics with Real osmium

**Files:**
- Create: `tests/fixtures/image_tag_coverage.osm`
- Create: `tests/test_real_osmium_extraction.py`

- [ ] Create a deterministic OSM XML fixture containing:
  - a node with `image` (excluded);
  - an open way with `image` (excluded);
  - an untagged closed building (exported by osmium but predicate-excluded);
  - closed standard-area ways, one for each target key (included);
  - a closed `area=no` way with `image` (excluded);
  - a multipolygon relation with `wikimedia_commons` on the relation only
    (relation included; untagged member ways not matched);
  - a boundary relation with `flickr` on the relation (included).
- [ ] Write an integration test that uses the real `osmium` binary to convert
  XML to PBF, streams the PBF through the packaged policy, filters with
  `has_target_tag`, and asserts the exact `(osm_type, osm_id)` inclusion set and
  exact full tag maps.
- [ ] Mark the test `integration`; fail rather than skip when `osmium` is absent,
  because production readiness requires the executable.
- [ ] Run the integration test and observe RED before fixture/policy correction.
- [ ] Make only fixture or extraction-policy corrections required for GREEN.
- [ ] Run:

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
git diff --check
```

- [ ] Inspect the full diff and verify there are no writes to the source PBF
  tree, generated-data root, sibling repositories, or any remote.
- [ ] Commit `test: prove real osmium image-tag area contract` and stop at the
  Phase 2 gate.
