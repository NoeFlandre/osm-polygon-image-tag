import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from osm_polygon_image_tag.ingest.extraction import (
    SourceTagRecord,
    restore_original_tags,
    scan_target_source_tags,
    stream_export,
)
from osm_polygon_image_tag.resources import osmium_export_config
from osm_polygon_image_tag.storage import validate_geoparquet, write_geoparquet
from osm_polygon_image_tag.ingest.transform import AcceptedRow, transform_record

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
def test_real_osmium_builds_exact_lossless_geoparquet_shard(tmp_path: Path) -> None:
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

    def accepted_rows() -> Iterator[dict[str, Any]]:
        records = restore_original_tags(
            stream_export(pbf_path, osmium_export_config(), executable=executable),
            lookup=lambda osm_type, osm_id: source_by_identity.get((osm_type, osm_id)),
        )
        for record in records:
            outcome = transform_record(record, source_pbf="coverage.osm.pbf")
            if isinstance(outcome, AcceptedRow):
                yield outcome.values

    shard = tmp_path / "generated" / "data" / "coverage.parquet"
    result = write_geoparquet(accepted_rows(), shard, batch_size=3)
    validate_geoparquet(shard)
    table = pq.read_table(shard)

    assert result.row_count == 9
    identities = set(
        zip(
            table.column("osm_type").to_pylist(),
            table.column("osm_id").to_pylist(),
            strict=True,
        )
    )
    assert identities == EXPECTED
    assert all(area > 0 for area in table.column("area_m2").to_pylist())
    assert set(table.column("geometry_type").to_pylist()) == {"Polygon", "MultiPolygon"}
    rows = table.to_pylist()
    relation = next(row for row in rows if row["osm_type"] == "relation" and row["osm_id"] == 2000)
    assert dict(relation["tags"]) == {
        "name": "Relation area",
        "type": "multipolygon",
        "wikimedia_commons": "Category:Relation",
    }
    assert relation["wikimedia_commons"] == "Category:Relation"
    indexed = next(row for row in rows if row["osm_type"] == "way" and row["osm_id"] == 1001)
    assert indexed["bubbleid"] == "bing-streetside-id"
    assert dict(indexed["panoramax_values"]) == {
        "panoramax:0": "panoramax-first",
        "panoramax:2": "panoramax-third",
    }
    assert all(row["source_pbf"] == "coverage.osm.pbf" for row in rows)
