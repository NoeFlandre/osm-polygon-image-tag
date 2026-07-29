# Phase 1 Project Foundation and Immutable Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create an independent, installable `uv` project with typed path boundaries, deterministic read-only PBF discovery, and a non-mutating local preflight command.

**Architecture:** This phase establishes only the package and safety perimeter. `PipelinePaths` owns canonical source/output separation, `discover_pbfs` performs deterministic read-only inventory, and `run_preflight` composes injected system probes into canonical JSON; no extraction, GeoParquet, manifest, catalog, publication, or remote code is introduced.

**Tech Stack:** Python 3.12, uv, hatchling, pytest, pytest-cov, Ruff, mypy, standard-library `pathlib`, `os`, `shutil`, `subprocess`, and `json`.

---

## Scope and Stop Condition

This plan implements Phase 1 of the approved design only:

- independent package and repository metadata;
- immutable source/output path boundaries;
- deterministic `.osm.pbf` discovery;
- read-only tool, path, inventory, and capacity preflight;
- a `preflight` CLI command.

It explicitly does not implement `osmium` extraction, tag filtering, geometry,
Parquet, manifests, resumability, signals, statistics, dataset cards, Hugging
Face, GitHub, or any real data/output mutation.

Stop after all Phase 1 gates pass and the phase commit set has been reviewed.
Do not begin Phase 2 without explicit approval.

## File Map

- `pyproject.toml`: package metadata, dependencies, entry point, and tool gates.
- `README.md`: public project scope, safety boundary, and Phase 1 command.
- `LICENSE`: independent Apache-2.0 code license.
- `.gitignore`: local environments, caches, and generated artifacts.
- `src/osm_polygon_image_tag/__init__.py`: public package version.
- `src/osm_polygon_image_tag/py.typed`: typed-package marker.
- `src/osm_polygon_image_tag/errors.py`: typed user-facing configuration and preflight errors.
- `src/osm_polygon_image_tag/config.py`: canonical immutable path contract.
- `src/osm_polygon_image_tag/discovery.py`: read-only deterministic PBF inventory.
- `src/osm_polygon_image_tag/preflight.py`: injected system probes and canonical report.
- `src/osm_polygon_image_tag/cli.py`: argument parsing and exit-code boundary.
- `tests/test_project_foundation.py`: package metadata and independence.
- `tests/test_config.py`: overlap, traversal, and symlink boundary behavior.
- `tests/test_discovery.py`: deterministic inventory and unsafe-entry rejection.
- `tests/test_preflight.py`: probe composition and zero-write behavior.
- `tests/test_cli.py`: CLI output and error contract.

### Task 1: Bootstrap the Independent uv Package

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `LICENSE`
- Create: `src/osm_polygon_image_tag/__init__.py`
- Create: `src/osm_polygon_image_tag/py.typed`
- Create: `tests/test_project_foundation.py`

- [ ] **Step 1: Create the minimal build metadata needed to run tests**

Create `pyproject.toml`:

```toml
[project]
name = "osm-polygon-image-tag"
version = "0.1.0"
description = "Reproducible GeoParquet of OpenStreetMap polygons carrying image-reference tags."
readme = "README.md"
requires-python = ">=3.12"
authors = [{ name = "Noé Flandre" }]
license = "Apache-2.0"
keywords = ["openstreetmap", "geoparquet", "geospatial", "dataset", "images"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: Apache Software License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.12",
    "Typing :: Typed",
]
dependencies = []

[project.scripts]
osm-polygon-image-tag = "osm_polygon_image_tag.cli:run"

[project.urls]
Source = "https://github.com/NoeFlandre/osm-polygon-image-tag"
Issues = "https://github.com/NoeFlandre/osm-polygon-image-tag/issues"
Dataset = "https://huggingface.co/datasets/NoeFlandre/osm-polygon-image-tag"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/osm_polygon_image_tag"]

[dependency-groups]
dev = [
    "mypy>=1.17,<2",
    "pytest>=8.4,<9",
    "pytest-cov>=6.2,<7",
    "ruff>=0.12,<0.13",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "RUF", "S", "TID"]
ignore = ["S101"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
line-ending = "lf"

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["osm_polygon_image_tag"]

[tool.coverage.run]
branch = true
source = ["osm_polygon_image_tag"]

[tool.coverage.report]
fail_under = 90
show_missing = true
```

Create `README.md`:

````markdown
# OSM Polygon Image Tag

An independent, reproducible pipeline for a GeoParquet dataset of OpenStreetMap
area features carrying raw image-reference tags.

The project reads PBF input from
`/Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw` and reserves
`/Volumes/Seagate M3/projects/osm-polygon-image-tag` for generated state. Input
PBFs are immutable. Phase 1 provides only a read-only preflight:

```bash
uv run osm-polygon-image-tag preflight \
  --source-root "/Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw" \
  --data-root "/Volumes/Seagate M3/projects/osm-polygon-image-tag"
```

No provider APIs are called and no images are downloaded.

## License

Pipeline code is Apache-2.0. OpenStreetMap-derived data remains subject to the
Open Database License; see the generated dataset card once publication support
is implemented.
````

Create `.gitignore`:

```gitignore
.DS_Store
.coverage
.mypy_cache/
.pytest_cache/
.ruff_cache/
.venv/
__pycache__/
*.py[cod]
build/
dist/
```

Copy the unmodified Apache-2.0 text from the independent sibling's public code
license into `LICENSE`:

```bash
cp /Users/noeflandre/osm-polygon-description-tag/LICENSE LICENSE
```

Create the `src/osm_polygon_image_tag/` directory, an empty
`src/osm_polygon_image_tag/__init__.py`, and an empty
`src/osm_polygon_image_tag/py.typed`. Deliberately leave `__init__.py` empty
until after the first failing behavior test.

- [ ] **Step 2: Lock and install the bootstrap environment**

Run:

```bash
uv lock
uv sync
```

Expected: both commands exit `0`, `uv.lock` exists, and the editable package is
installed in `.venv`.

- [ ] **Step 3: Write the failing foundation test**

Create `tests/test_project_foundation.py`:

```python
from importlib.metadata import metadata, version
from pathlib import Path

import osm_polygon_image_tag


def test_distribution_and_package_versions_match() -> None:
    assert version("osm-polygon-image-tag") == "0.1.0"
    assert osm_polygon_image_tag.__version__ == "0.1.0"


def test_public_metadata_targets_only_this_project() -> None:
    project = metadata("osm-polygon-image-tag")
    assert project["Name"] == "osm-polygon-image-tag"
    assert "image-reference tags" in project["Summary"]

    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "osm-polygon-description-tag" not in pyproject
    assert "osm-polygon-wikidata-only" not in pyproject
```

- [ ] **Step 4: Run the test and observe RED**

Run:

```bash
uv run pytest tests/test_project_foundation.py -q
```

Expected: FAIL because the package has no `__version__`.

- [ ] **Step 5: Add the minimal package implementation and verify GREEN**

Replace the empty `src/osm_polygon_image_tag/__init__.py` with:

```python
"""Build a reproducible OSM polygon image-reference dataset."""

__version__ = "0.1.0"
```

Run:

```bash
uv run pytest tests/test_project_foundation.py -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit the independent foundation**

```bash
git add .gitignore LICENSE README.md pyproject.toml uv.lock \
  src/osm_polygon_image_tag/__init__.py src/osm_polygon_image_tag/py.typed \
  tests/test_project_foundation.py
git commit -m "build: initialize independent image-tag project"
```

### Task 2: Enforce Immutable Path Boundaries

**Files:**
- Create: `src/osm_polygon_image_tag/errors.py`
- Create: `src/osm_polygon_image_tag/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing boundary tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

import pytest

from osm_polygon_image_tag.config import PipelinePaths
from osm_polygon_image_tag.errors import ConfigurationError


def test_accepts_separate_existing_source_and_output(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    output = tmp_path / "image-data"
    source.mkdir()

    paths = PipelinePaths.build(source_root=source, data_root=output)

    assert paths.source_root == source.resolve()
    assert paths.data_root == output.resolve()
    assert not output.exists()


@pytest.mark.parametrize(
    ("source_suffix", "output_suffix"),
    [
        ("raw", "raw"),
        ("raw", "raw/output"),
        ("raw/nested", "raw"),
    ],
)
def test_rejects_equal_or_nested_roots(
    tmp_path: Path, source_suffix: str, output_suffix: str
) -> None:
    source = tmp_path / source_suffix
    source.mkdir(parents=True)

    with pytest.raises(ConfigurationError, match="must not overlap"):
        PipelinePaths.build(
            source_root=source,
            data_root=tmp_path / output_suffix,
        )


def test_resolves_parent_traversal_before_boundary_check(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()

    with pytest.raises(ConfigurationError, match="must not overlap"):
        PipelinePaths.build(source_root=source, data_root=source / ".." / "raw" / "out")


def test_rejects_a_symlinked_source_root(tmp_path: Path) -> None:
    real_source = tmp_path / "real-raw"
    real_source.mkdir()
    linked_source = tmp_path / "linked-raw"
    linked_source.symlink_to(real_source, target_is_directory=True)

    with pytest.raises(ConfigurationError, match="source root must not be a symlink"):
        PipelinePaths.build(source_root=linked_source, data_root=tmp_path / "output")


def test_rejects_a_source_root_that_is_not_a_directory(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.write_bytes(b"not a directory")

    with pytest.raises(ConfigurationError, match="source root must be a directory"):
        PipelinePaths.build(source_root=source, data_root=tmp_path / "output")


def test_build_never_creates_output_directories(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    output = tmp_path / "output"
    source.mkdir()

    PipelinePaths.build(source_root=source, data_root=output)

    assert list(tmp_path.iterdir()) == [source]
```

- [ ] **Step 2: Run the tests to verify RED**

Run:

```bash
uv run pytest tests/test_config.py -q
```

Expected: collection ERROR with
`ModuleNotFoundError: No module named 'osm_polygon_image_tag.config'`.

- [ ] **Step 3: Add typed errors and the minimal immutable path contract**

Create `src/osm_polygon_image_tag/errors.py`:

```python
class ImageTagPipelineError(Exception):
    """Base class for expected operator-facing failures."""


class ConfigurationError(ImageTagPipelineError):
    """Raised when configured paths violate the storage contract."""
```

Create `src/osm_polygon_image_tag/config.py`:

```python
from dataclasses import dataclass
from pathlib import Path

from osm_polygon_image_tag.errors import ConfigurationError


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


@dataclass(frozen=True, slots=True)
class PipelinePaths:
    source_root: Path
    data_root: Path

    @classmethod
    def build(cls, *, source_root: Path, data_root: Path) -> "PipelinePaths":
        if source_root.is_symlink():
            raise ConfigurationError("source root must not be a symlink")
        try:
            canonical_source = source_root.expanduser().resolve(strict=True)
        except OSError as error:
            raise ConfigurationError(f"source root is unavailable: {source_root}") from error
        if not canonical_source.is_dir():
            raise ConfigurationError("source root must be a directory")

        canonical_data = data_root.expanduser().resolve(strict=False)
        if _overlaps(canonical_source, canonical_data):
            raise ConfigurationError("source root and data root must not overlap")
        return cls(source_root=canonical_source, data_root=canonical_data)
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_config.py -q
```

Expected: `8 passed`.

- [ ] **Step 5: Run foundation regression tests**

Run:

```bash
uv run pytest tests/test_project_foundation.py tests/test_config.py -q
```

Expected: `10 passed`.

- [ ] **Step 6: Commit path boundaries**

```bash
git add src/osm_polygon_image_tag/errors.py \
  src/osm_polygon_image_tag/config.py tests/test_config.py
git commit -m "feat: enforce immutable storage boundaries"
```

### Task 3: Discover PBF Inputs Deterministically Without Writes

**Files:**
- Create: `src/osm_polygon_image_tag/discovery.py`
- Create: `tests/test_discovery.py`

- [ ] **Step 1: Write failing inventory tests**

Create `tests/test_discovery.py`:

```python
from pathlib import Path, PurePosixPath

import pytest

from osm_polygon_image_tag.discovery import PbfSource, discover_pbfs
from osm_polygon_image_tag.errors import ConfigurationError


def test_discovers_only_pbf_files_in_relative_path_order(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    (source / "z").mkdir(parents=True)
    (source / "a").mkdir()
    (source / "z" / "two.osm.pbf").write_bytes(b"22")
    (source / "a" / "one.osm.pbf").write_bytes(b"1")
    (source / "ignore.txt").write_text("x", encoding="utf-8")

    assert discover_pbfs(source) == (
        PbfSource(
            relative_path=PurePosixPath("a/one.osm.pbf"),
            absolute_path=(source / "a" / "one.osm.pbf").resolve(),
            size_bytes=1,
        ),
        PbfSource(
            relative_path=PurePosixPath("z/two.osm.pbf"),
            absolute_path=(source / "z" / "two.osm.pbf").resolve(),
            size_bytes=2,
        ),
    )


def test_empty_inventory_is_valid_and_read_only(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    before = tuple(source.iterdir())

    assert discover_pbfs(source) == ()
    assert tuple(source.iterdir()) == before


def test_rejects_symlink_anywhere_in_source_tree(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    target = tmp_path / "outside.osm.pbf"
    target.write_bytes(b"x")
    (source / "linked.osm.pbf").symlink_to(target)

    with pytest.raises(ConfigurationError, match="symlink"):
        discover_pbfs(source)


def test_rejects_non_regular_matching_entry(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    (source / "named-pipe.osm.pbf").mkfifo()

    with pytest.raises(ConfigurationError, match="regular file"):
        discover_pbfs(source)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run pytest tests/test_discovery.py -q
```

Expected: collection ERROR with
`ModuleNotFoundError: No module named 'osm_polygon_image_tag.discovery'`.

- [ ] **Step 3: Implement the bounded deterministic walk**

Create `src/osm_polygon_image_tag/discovery.py`:

```python
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from osm_polygon_image_tag.errors import ConfigurationError


@dataclass(frozen=True, slots=True, order=True)
class PbfSource:
    relative_path: PurePosixPath
    absolute_path: Path
    size_bytes: int


def discover_pbfs(source_root: Path) -> tuple[PbfSource, ...]:
    root = source_root.resolve(strict=True)
    discovered: list[PbfSource] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(directory)
        for name in sorted((*directory_names, *file_names)):
            candidate = current / name
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ConfigurationError(f"source tree contains a symlink: {candidate}")

        directory_names.sort()
        for name in sorted(file_names):
            if not name.endswith(".osm.pbf"):
                continue
            candidate = current / name
            details = candidate.stat(follow_symlinks=False)
            if not stat.S_ISREG(details.st_mode):
                raise ConfigurationError(f"PBF entry is not a regular file: {candidate}")
            discovered.append(
                PbfSource(
                    relative_path=PurePosixPath(candidate.relative_to(root).as_posix()),
                    absolute_path=candidate.resolve(strict=True),
                    size_bytes=details.st_size,
                )
            )
    return tuple(sorted(discovered))
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_discovery.py -q
```

Expected: `4 passed`.

- [ ] **Step 5: Verify no discovery API mutates the source**

Run:

```bash
uv run pytest tests/test_config.py tests/test_discovery.py -q
```

Expected: `11 passed`.

- [ ] **Step 6: Commit discovery**

```bash
git add src/osm_polygon_image_tag/discovery.py tests/test_discovery.py
git commit -m "feat: discover PBF inputs without mutation"
```

### Task 4: Compose a Read-Only Preflight Report

**Files:**
- Create: `src/osm_polygon_image_tag/preflight.py`
- Create: `tests/test_preflight.py`

- [ ] **Step 1: Write failing preflight tests**

Create `tests/test_preflight.py`:

```python
import subprocess
from pathlib import Path

import pytest

from osm_polygon_image_tag.config import PipelinePaths
from osm_polygon_image_tag.errors import PreflightError
from osm_polygon_image_tag.preflight import (
    Capacity,
    PreflightReport,
    ToolVersion,
    probe_capacity,
    probe_osmium,
    run_preflight,
)


def test_preflight_composes_exact_inventory_without_creating_output(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    output = tmp_path / "output"
    source.mkdir()
    (source / "region.osm.pbf").write_bytes(b"pbf")
    paths = PipelinePaths.build(source_root=source, data_root=output)

    report = run_preflight(
        paths,
        probe_osmium=lambda: ToolVersion(path="/opt/homebrew/bin/osmium", version="1.19.1"),
        probe_capacity=lambda _path: Capacity(free_bytes=10_000, total_bytes=20_000),
    )

    assert report == PreflightReport(
        source_root=str(source.resolve()),
        data_root=str(output.resolve()),
        pbf_count=1,
        pbf_bytes=3,
        osmium=ToolVersion(path="/opt/homebrew/bin/osmium", version="1.19.1"),
        capacity=Capacity(free_bytes=10_000, total_bytes=20_000),
    )
    assert not output.exists()


def test_preflight_rejects_missing_osmium(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    paths = PipelinePaths.build(source_root=source, data_root=tmp_path / "output")

    def missing_osmium() -> ToolVersion:
        raise PreflightError("required executable not found: osmium")

    with pytest.raises(PreflightError, match="required executable"):
        run_preflight(
            paths,
            probe_osmium=missing_osmium,
            probe_capacity=lambda _path: Capacity(free_bytes=1, total_bytes=2),
        )


def test_real_osmium_probe_reports_first_version_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("osm_polygon_image_tag.preflight.shutil.which", lambda _name: "/bin/osmium")
    monkeypatch.setattr(
        "osm_polygon_image_tag.preflight.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["/bin/osmium", "--version"],
            returncode=0,
            stdout="osmium version 1.19.1\nlibosmium 2.x\n",
            stderr="",
        ),
    )

    assert probe_osmium() == ToolVersion(path="/bin/osmium", version="osmium version 1.19.1")


def test_real_osmium_probe_rejects_missing_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("osm_polygon_image_tag.preflight.shutil.which", lambda _name: None)

    with pytest.raises(PreflightError, match="required executable"):
        probe_osmium()


@pytest.mark.parametrize(
    ("returncode", "stdout", "message"),
    [
        (2, "", "failed with exit 2"),
        (0, "", "returned no version text"),
    ],
)
def test_real_osmium_probe_rejects_unusable_results(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    message: str,
) -> None:
    monkeypatch.setattr("osm_polygon_image_tag.preflight.shutil.which", lambda _name: "/bin/osmium")
    monkeypatch.setattr(
        "osm_polygon_image_tag.preflight.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["/bin/osmium", "--version"],
            returncode=returncode,
            stdout=stdout,
            stderr="failure",
        ),
    )

    with pytest.raises(PreflightError, match=message):
        probe_osmium()


def test_capacity_probe_uses_nearest_existing_parent(tmp_path: Path) -> None:
    capacity = probe_capacity(tmp_path / "not-created" / "output")

    assert capacity.free_bytes > 0
    assert capacity.total_bytes >= capacity.free_bytes
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run pytest tests/test_preflight.py -q
```

Expected: collection ERROR because `PreflightError` and `preflight` do not
exist.

- [ ] **Step 3: Add the error type**

Append to `src/osm_polygon_image_tag/errors.py`:

```python
class PreflightError(ImageTagPipelineError):
    """Raised when a read-only environment check fails."""
```

- [ ] **Step 4: Implement injected read-only probes**

Create `src/osm_polygon_image_tag/preflight.py`:

```python
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from osm_polygon_image_tag.config import PipelinePaths
from osm_polygon_image_tag.discovery import discover_pbfs
from osm_polygon_image_tag.errors import PreflightError


@dataclass(frozen=True, slots=True)
class ToolVersion:
    path: str
    version: str


@dataclass(frozen=True, slots=True)
class Capacity:
    free_bytes: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class PreflightReport:
    source_root: str
    data_root: str
    pbf_count: int
    pbf_bytes: int
    osmium: ToolVersion
    capacity: Capacity

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probe_osmium() -> ToolVersion:
    executable = shutil.which("osmium")
    if executable is None:
        raise PreflightError("required executable not found: osmium")
    completed = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise PreflightError(f"osmium --version failed with exit {completed.returncode}")
    first_line = completed.stdout.splitlines()
    if not first_line:
        raise PreflightError("osmium --version returned no version text")
    return ToolVersion(path=executable, version=first_line[0].strip())


def probe_capacity(path: Path) -> Capacity:
    anchor = path
    while not anchor.exists():
        if anchor.parent == anchor:
            raise PreflightError(f"no existing capacity anchor for: {path}")
        anchor = anchor.parent
    usage = shutil.disk_usage(anchor)
    return Capacity(free_bytes=usage.free, total_bytes=usage.total)


def run_preflight(
    paths: PipelinePaths,
    *,
    probe_osmium: Callable[[], ToolVersion] = probe_osmium,
    probe_capacity: Callable[[Path], Capacity] = probe_capacity,
) -> PreflightReport:
    sources = discover_pbfs(paths.source_root)
    return PreflightReport(
        source_root=str(paths.source_root),
        data_root=str(paths.data_root),
        pbf_count=len(sources),
        pbf_bytes=sum(source.size_bytes for source in sources),
        osmium=probe_osmium(),
        capacity=probe_capacity(paths.data_root),
    )
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_preflight.py -q
```

Expected: `7 passed`.

- [ ] **Step 6: Run Phase 1 regressions**

Run:

```bash
uv run pytest tests/test_config.py tests/test_discovery.py tests/test_preflight.py -q
```

Expected: `19 passed`.

- [ ] **Step 7: Commit the preflight service**

```bash
git add src/osm_polygon_image_tag/errors.py \
  src/osm_polygon_image_tag/preflight.py tests/test_preflight.py
git commit -m "feat: add read-only environment preflight"
```

### Task 5: Expose the Preflight CLI and Close Phase 1

**Files:**
- Create: `src/osm_polygon_image_tag/cli.py`
- Create: `tests/test_cli.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_cli.py`:

```python
import json
from pathlib import Path

from osm_polygon_image_tag.cli import run
from osm_polygon_image_tag.preflight import Capacity, PreflightReport, ToolVersion


def test_preflight_command_emits_canonical_json(
    tmp_path: Path, capsys: object
) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    output = tmp_path / "output"
    expected = PreflightReport(
        source_root=str(source.resolve()),
        data_root=str(output.resolve()),
        pbf_count=0,
        pbf_bytes=0,
        osmium=ToolVersion(path="/usr/bin/osmium", version="osmium 1.19.1"),
        capacity=Capacity(free_bytes=5, total_bytes=10),
    )

    exit_code = run(
        ["preflight", "--source-root", str(source), "--data-root", str(output)],
        execute_preflight=lambda _paths: expected,
    )

    assert exit_code == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.err == ""
    assert captured.out == json.dumps(expected.to_dict(), sort_keys=True) + "\n"


def test_expected_operator_error_returns_exit_two(
    tmp_path: Path, capsys: object
) -> None:
    missing = tmp_path / "missing"

    exit_code = run(
        ["preflight", "--source-root", str(missing), "--data-root", str(tmp_path / "out")]
    )

    assert exit_code == 2
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.out == ""
    assert "error:" in captured.err
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run pytest tests/test_cli.py -q
```

Expected: collection ERROR with
`ModuleNotFoundError: No module named 'osm_polygon_image_tag.cli'`.

- [ ] **Step 3: Implement the minimal CLI boundary**

Create `src/osm_polygon_image_tag/cli.py`:

```python
import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from osm_polygon_image_tag.config import PipelinePaths
from osm_polygon_image_tag.errors import ImageTagPipelineError
from osm_polygon_image_tag.preflight import PreflightReport, run_preflight


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="osm-polygon-image-tag")
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--source-root", type=Path, required=True)
    preflight.add_argument("--data-root", type=Path, required=True)
    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    execute_preflight: Callable[[PipelinePaths], PreflightReport] = run_preflight,
) -> int:
    arguments = _parser().parse_args(argv)
    try:
        paths = PipelinePaths.build(
            source_root=arguments.source_root,
            data_root=arguments.data_root,
        )
        report = execute_preflight(paths)
    except ImageTagPipelineError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), sort_keys=True))
    return 0
```

- [ ] **Step 4: Correct the typed capture fixture before running**

Replace the two `capsys: object` annotations and type-ignore comments with the
real pytest type:

```python
from _pytest.capture import CaptureFixture
```

Both test signatures must use `capsys: CaptureFixture[str]`, and both reads
must be plain `captured = capsys.readouterr()`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_cli.py -q
```

Expected: `2 passed`.

- [ ] **Step 6: Verify the installed entry point help**

Run:

```bash
uv run osm-polygon-image-tag --help
```

Expected: exit `0` and output containing `preflight`.

- [ ] **Step 7: Run all Phase 1 quality gates**

Run exactly:

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
```

Expected:

- `uv sync`: exit `0`;
- pytest: `23 passed`, coverage at least 90%;
- Ruff lint: `All checks passed!`;
- Ruff format: all files already formatted;
- mypy: `Success: no issues found`.

If a gate fails, use `superpowers:systematic-debugging`; add a focused failing
regression test before changing runtime behavior, then rerun the complete gate.

- [ ] **Step 8: Inspect the exact Phase 1 diff**

Run:

```bash
git status --short
git diff --check
git diff --stat
git diff
```

Expected: only Phase 1 package, documentation, lockfile, and tests are changed;
no generated data, sibling-repository file, volume file, or remote configuration
is present.

- [ ] **Step 9: Commit the CLI and Phase 1 closure**

```bash
git add README.md src/osm_polygon_image_tag/cli.py tests/test_cli.py
git commit -m "feat: expose safe preflight command"
```

- [ ] **Step 10: Verify committed state and stop**

Run:

```bash
git status --short --branch
git log --oneline --decorate -6
```

Expected: clean `main` branch with the Phase 1 commits. Stop and request review;
do not implement extraction or contact GitHub, Hugging Face, Geofabrik, or the
Seagate data root.

## Plan Self-Review

- Spec coverage: Phase 1 package isolation, path non-overlap, symlink/traversal
  rejection, deterministic read-only PBF discovery, tool/capacity preflight,
  public CLI, exact `uv` gates, and the phase stop condition are covered.
- Deliberate later scope: extraction, GeoParquet, atomic manifests, signals,
  reporting, publication, and live operations remain in later approved plans.
- Type consistency: `PipelinePaths`, `PbfSource`, `ToolVersion`, `Capacity`,
  `PreflightReport`, `run_preflight`, and `run` have one definition and matching
  use sites.
- Repository isolation: no runtime import or shared state is introduced; the
  only initialization copy is the standard Apache-2.0 license text.
