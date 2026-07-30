import shutil
import subprocess
from pathlib import Path

import pytest

from osm_polygon_image_tag.ingest.extraction import (
    SourceTagRecord,
    restore_original_tags,
    scan_target_source_tags,
    stream_export,
)
from osm_polygon_image_tag.runtime.resources import osmium_export_config

FIXTURE = Path("tests/fixtures/image_tag_coverage.osm")
EXPECTED = {
    ("way", 1001),
    ("way", 1100),
    ("way", 1101),
    ("way", 1102),
    ("way", 1103),
    ("way", 1104),
    ("way", 1105),
    ("relation", 2000),
    ("relation", 2001),
}


@pytest.mark.integration
def test_real_osmium_emits_exact_image_tag_area_set_and_all_tags(tmp_path: Path) -> None:
    executable = shutil.which("osmium")
    assert executable is not None, "production readiness requires osmium on PATH"
    pbf_path = tmp_path / "coverage.osm.pbf"
    subprocess.run(  # noqa: S603 - executable and fixture argv are controlled.
        [executable, "cat", str(FIXTURE), "-o", str(pbf_path)],
        check=True,
        capture_output=True,
        shell=False,
        timeout=30,
    )

    source_tags: list[SourceTagRecord] = []
    scan_target_source_tags(pbf_path, emit=source_tags.append)
    source_by_identity = {(record.osm_type, record.osm_id): record.tags for record in source_tags}
    records = list(
        restore_original_tags(
            stream_export(
                pbf_path,
                osmium_export_config(),
                executable=executable,
            ),
            lookup=lambda osm_type, osm_id: source_by_identity.get((osm_type, osm_id)),
        )
    )

    assert {(record.osm_type, record.osm_id) for record in records} == EXPECTED
    by_identity = {(record.osm_type, record.osm_id): record.tags for record in records}
    assert by_identity[("way", 1100)] == {
        "building": "yes",
        "image": "File:Exact View.jpg",
        "name": "Image building",
    }
    assert by_identity[("way", 1001)]["panoramax:0"] == "panoramax-first"
    assert by_identity[("way", 1001)]["panoramax:2"] == "panoramax-third"
    assert by_identity[("way", 1001)]["bubbleid"] == "bing-streetside-id"
    assert by_identity[("relation", 2000)] == {
        "type": "multipolygon",
        "wikimedia_commons": "Category:Relation",
        "name": "Relation area",
    }
    assert source_by_identity[("way", 1000)] == {"image": "open-way.jpg"}
    assert source_by_identity[("way", 1106)] == {
        "building": "yes",
        "area": "no",
        "image": "excluded.jpg",
    }
    assert ("way", 1000) not in by_identity
    assert ("way", 1106) not in by_identity
    assert ("way", 1200) not in by_identity
    assert ("way", 1201) not in by_identity
