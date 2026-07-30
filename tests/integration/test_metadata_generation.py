"""Integration coverage for metadata generated from a real osmium shard."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from osm_polygon_image_tag.artifacts.reporting import generate_metadata
from osm_polygon_image_tag.core.config import PipelinePaths
from osm_polygon_image_tag.ingest.discovery import discover_pbfs
from osm_polygon_image_tag.runtime.pipeline import build_one

FIXTURE = Path("tests/fixtures/image_tag_coverage.osm")


@pytest.mark.integration
def test_real_shard_produces_exact_global_statistics_and_stable_card(tmp_path: Path) -> None:
    executable = shutil.which("osmium")
    assert executable is not None
    source_root = tmp_path / "raw"
    source_root.mkdir()
    pbf = source_root / "coverage.osm.pbf"
    subprocess.run(  # noqa: S603 - controlled fixture argv.
        [executable, "cat", str(FIXTURE), "-o", str(pbf)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    paths = PipelinePaths.build(source_root=source_root, data_root=tmp_path / "generated")
    build_one(discover_pbfs(source_root)[0], paths, batch_size=3)

    result = generate_metadata(paths.data_root)
    first_card = result.card_path.read_bytes()
    first_statistics = result.statistics_path.read_bytes()
    statistics = json.loads(first_statistics)
    generate_metadata(paths.data_root)

    assert statistics["shards"] == 1
    assert statistics["rows"] == 9
    assert statistics["osm_types"] == {"relation": 2, "way": 7}
    assert statistics["geometry_types"] == {"MultiPolygon": 2, "Polygon": 7}
    assert statistics["provider_counts"] == {
        "bubbleid": 1,
        "flickr": 2,
        "image": 1,
        "kartaview": 1,
        "mapillary": 1,
        "panoramax": 2,
        "wikimedia_commons": 2,
    }
    assert statistics["duplicate_observations"] == 0
    assert result.card_path.read_bytes() == first_card
    assert result.statistics_path.read_bytes() == first_statistics
