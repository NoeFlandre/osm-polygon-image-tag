from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely import to_wkb
from shapely.geometry import Polygon

from osm_polygon_image_tag.extraction import ExportRecord
from osm_polygon_image_tag.storage import StorageError, validate_geoparquet, write_geoparquet
from osm_polygon_image_tag.transform import AcceptedRow, transform_record


def _row(osm_id: int) -> dict[str, Any]:
    record = ExportRecord(
        geometry_ewkb_hex=to_wkb(
            Polygon([(osm_id, 0), (osm_id + 0.1, 0), (osm_id + 0.1, 0.1), (osm_id, 0.1)]),
            hex=True,
        ),
        osm_type="way",
        osm_id=osm_id,
        version=1,
        changeset=2,
        timestamp="2026-01-01T00:00:00Z",
        tags={"name": f"Place {osm_id}", "image": f"{osm_id}.jpg"},
    )
    outcome = transform_record(record, source_pbf="region.osm.pbf")
    assert isinstance(outcome, AcceptedRow)
    return outcome.values


def test_writes_bounded_zstd_row_groups_and_preserves_schema(tmp_path: Path) -> None:
    final_path = tmp_path / "data" / "region.parquet"

    result = write_geoparquet((_row(index) for index in range(3)), final_path, batch_size=2)

    assert result.row_count == 3
    assert result.size_bytes == final_path.stat().st_size
    parquet = pq.ParquetFile(final_path)
    assert parquet.metadata.num_row_groups == 2
    assert parquet.metadata.row_group(0).column(0).compression == "ZSTD"
    validate_geoparquet(final_path)
    table = pq.read_table(final_path)
    assert table.column("osm_id").to_pylist() == [0, 1, 2]
    assert dict(table.column("tags").to_pylist()[0]) == {"image": "0.jpg", "name": "Place 0"}
    assert table.schema.metadata is not None
    assert b"geo" in table.schema.metadata


def test_writes_a_valid_empty_shard(tmp_path: Path) -> None:
    final_path = tmp_path / "empty.parquet"

    result = write_geoparquet(iter(()), final_path, batch_size=2)

    assert result.row_count == 0
    assert pq.read_table(final_path).num_rows == 0
    validate_geoparquet(final_path)


def test_iterator_failure_leaves_no_final_or_temporary_file(tmp_path: Path) -> None:
    final_path = tmp_path / "data" / "failed.parquet"

    def failing_rows() -> Iterator[dict[str, Any]]:
        yield _row(1)
        raise RuntimeError("source failed")

    with pytest.raises(RuntimeError, match="source failed"):
        write_geoparquet(failing_rows(), final_path, batch_size=1)

    assert not final_path.exists()
    assert not list(final_path.parent.glob("*.tmp"))


def test_validation_rejects_schema_drift(tmp_path: Path) -> None:
    path = tmp_path / "wrong.parquet"
    pq.write_table(pa.table({"osm_id": [1]}), path)

    with pytest.raises(StorageError, match="schema"):
        validate_geoparquet(path)
