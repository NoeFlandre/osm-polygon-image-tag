import shutil
import subprocess
from pathlib import Path

import pytest

from osm_polygon_image_tag.config import PipelinePaths
from osm_polygon_image_tag.discovery import discover_pbfs
from osm_polygon_image_tag.manifest import file_sha256
from osm_polygon_image_tag.pipeline import build_one

FIXTURE = Path("tests/fixtures/image_tag_coverage.osm")


@pytest.mark.integration
def test_real_pipeline_second_run_reuses_identical_verified_shard(tmp_path: Path) -> None:
    executable = shutil.which("osmium")
    assert executable is not None
    source_root = tmp_path / "raw"
    source_root.mkdir()
    pbf = source_root / "coverage.osm.pbf"
    subprocess.run(  # noqa: S603 - controlled executable and fixture.
        [executable, "cat", str(FIXTURE), "-o", str(pbf)],
        check=True,
        capture_output=True,
        shell=False,
        timeout=30,
    )
    original_source_hash = file_sha256(pbf)
    paths = PipelinePaths.build(source_root=source_root, data_root=tmp_path / "generated")
    source = discover_pbfs(source_root)[0]

    first = build_one(source, paths, batch_size=3)
    first_output = first.output_path.read_bytes()
    first_manifest = first.manifest_path.read_bytes()
    second = build_one(source, paths, batch_size=3)

    assert first.status == "built"
    assert first.accepted_rows == 8
    assert second.status == "skipped"
    assert second.output_path.read_bytes() == first_output
    assert second.manifest_path.read_bytes() == first_manifest
    assert file_sha256(pbf) == original_source_hash
