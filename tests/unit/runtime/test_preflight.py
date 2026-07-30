import subprocess
from pathlib import Path

import pytest

from osm_polygon_image_tag.core.config import PipelinePaths
from osm_polygon_image_tag.core.errors import PreflightError
from osm_polygon_image_tag.runtime.preflight import (
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
    monkeypatch.setattr(
        "osm_polygon_image_tag.runtime.preflight.shutil.which", lambda _name: "/bin/osmium"
    )
    monkeypatch.setattr(
        "osm_polygon_image_tag.runtime.preflight.subprocess.run",
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
    monkeypatch.setattr("osm_polygon_image_tag.runtime.preflight.shutil.which", lambda _name: None)

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
    monkeypatch.setattr(
        "osm_polygon_image_tag.runtime.preflight.shutil.which", lambda _name: "/bin/osmium"
    )
    monkeypatch.setattr(
        "osm_polygon_image_tag.runtime.preflight.subprocess.run",
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
