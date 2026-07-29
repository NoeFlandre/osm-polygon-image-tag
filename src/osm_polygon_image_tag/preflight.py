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
    completed = subprocess.run(  # noqa: S603 - executable is resolved; argv is fixed.
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
