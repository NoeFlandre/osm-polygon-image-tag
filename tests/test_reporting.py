import json
import shutil
import subprocess
from pathlib import Path

import pytest

from osm_polygon_image_tag.config import PipelinePaths
from osm_polygon_image_tag.discovery import discover_pbfs
from osm_polygon_image_tag.pipeline import build_one
from osm_polygon_image_tag.reporting import generate_metadata

FIXTURE = Path("tests/fixtures/image_tag_coverage.osm")


def test_empty_metadata_is_deterministic_and_factual(tmp_path: Path) -> None:
    first = generate_metadata(tmp_path)
    first_json = first.statistics_path.read_bytes()
    first_card = first.card_path.read_bytes()
    second = generate_metadata(tmp_path)

    assert second.statistics_path.read_bytes() == first_json
    assert second.card_path.read_bytes() == first_card
    statistics = json.loads(first_json)
    assert statistics["shards"] == 0
    assert statistics["rows"] == 0
    assert statistics["provider_counts"] == {
        "flickr": 0,
        "image": 0,
        "kartaview": 0,
        "mapillary": 0,
        "panoramax": 0,
        "wikimedia_commons": 0,
    }
    assert b"Open Database License" in first_card
    assert b"does not establish image copyright" in first_card


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
    assert statistics["rows"] == 8
    assert statistics["osm_types"] == {"relation": 2, "way": 6}
    assert statistics["geometry_types"] == {"MultiPolygon": 2, "Polygon": 6}
    assert statistics["provider_counts"] == {
        "flickr": 2,
        "image": 1,
        "kartaview": 1,
        "mapillary": 1,
        "panoramax": 1,
        "wikimedia_commons": 2,
    }
    assert statistics["duplicate_observations"] == 0
    assert result.card_path.read_bytes() == first_card
    assert result.statistics_path.read_bytes() == first_statistics
