"""Tests for finalized polygon Parquet input handling."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely import to_wkb
from shapely.geometry import MultiPolygon, Polygon

from osm_polygon_image_tag.artifacts.geography.inputs import (
    GeometryCentroid,
    iter_polygon_geometry,
    read_polygon_centroids,
    read_shard_polygon_centroids,
)
from osm_polygon_image_tag.artifacts.geography.models import GeographicMapError
from osm_polygon_image_tag.artifacts.storage import write_geoparquet
from osm_polygon_image_tag.core.manifest import (
    DATASET_SCHEMA_VERSION,
    PROCESSING_CONTRACT_VERSION,
    Manifest,
    OutputIdentity,
    RunCounts,
    SourceIdentity,
    file_sha256,
    write_manifest,
)


def _write_polygon_shard(
    root: Path,
    relative_path: str,
    polygons: list[Polygon | MultiPolygon],
    *,
    image_value: str | None = None,
) -> Path:
    rows = []
    for index, polygon in enumerate(polygons, start=1):
        tags = {"image": image_value} if image_value else {"image": f"https://img.test/{index}.jpg"}
        rows.append(
            {
                "osm_type": "way",
                "osm_id": index,
                "osm_version": 1,
                "osm_changeset": 1,
                "osm_timestamp": None,
                "source_pbf": f"region-{relative_path}.osm.pbf",
                "source_feature_id": f"region-{relative_path}/way/{index}",
                "geometry": to_wkb(polygon),
                "geometry_type": ("Polygon" if isinstance(polygon, Polygon) else "MultiPolygon"),
                "area_m2": 1.0,
                "bbox_min_lon": polygon.bounds[0],
                "bbox_min_lat": polygon.bounds[1],
                "bbox_max_lon": polygon.bounds[2],
                "bbox_max_lat": polygon.bounds[3],
                "tags": tags,
                "image": image_value or f"https://img.test/{index}.jpg",
                "wikimedia_commons": None,
                "mapillary": None,
                "panoramax": None,
                "panoramax_values": {"panoramax": f"id-{index}"},
                "kartaview": None,
                "flickr": None,
                "bubbleid": None,
            }
        )
    output = root / relative_path
    write_geoparquet(rows, output)
    return output


def _build_shard(
    root: Path,
    relative_path: str,
    polygons: list[Polygon | MultiPolygon],
) -> tuple[Manifest, Path]:
    output = _write_polygon_shard(root, relative_path, polygons)
    manifest = Manifest(
        manifest_schema_version=1,
        processing_contract_version=PROCESSING_CONTRACT_VERSION,
        dataset_schema_version=DATASET_SCHEMA_VERSION,
        source=SourceIdentity(f"region-{relative_path}.osm.pbf", 1, 1, "a" * 64),
        output=OutputIdentity(
            relative_path,
            output.stat().st_size,
            file_sha256(output),
            len(polygons),
        ),
        osmium_version="test",
        counts=RunCounts(len(polygons), {}),
    )
    write_manifest(
        manifest,
        root / "manifests" / f"{relative_path.replace('/', '-')}.manifest.json",
    )
    return manifest, output


def test_read_polygon_centroids_decodes_polygon_and_multipolygon(tmp_path: Path) -> None:
    polygon = Polygon([(4.0, 50.0), (4.1, 50.0), (4.1, 50.1), (4.0, 50.1)])
    multipolygon = MultiPolygon(
        [
            Polygon([(2.0, 48.0), (2.1, 48.0), (2.1, 48.1), (2.0, 48.1)]),
            Polygon([(10.0, 60.0), (10.1, 60.0), (10.1, 60.1), (10.0, 60.1)]),
        ]
    )
    _build_shard(tmp_path, "data/region-1.parquet", [polygon, multipolygon])

    centroids = list(read_polygon_centroids(tmp_path))

    assert len(centroids) == 2
    # Centroids remain derived only from the geometry, not the bounding box.
    for centroid in centroids:
        assert isinstance(centroid, GeometryCentroid)
        assert -180.0 <= centroid.lon <= 180.0
        assert -90.0 <= centroid.lat <= 90.0
    centroids.sort(key=lambda c: (c.shard_relative_path, c.row_index))
    assert any(c.geometry_type == "Polygon" for c in centroids)
    assert any(c.geometry_type == "MultiPolygon" for c in centroids)


def test_read_polygon_centroids_includes_per_shard_relative_path_and_index(
    tmp_path: Path,
) -> None:
    polygon_a = Polygon([(4.0, 50.0), (4.1, 50.0), (4.1, 50.1), (4.0, 50.1)])
    polygon_b = Polygon([(5.0, 51.0), (5.1, 51.0), (5.1, 51.1), (5.0, 51.1)])
    _build_shard(tmp_path, "data/region-1.parquet", [polygon_a])
    _build_shard(tmp_path, "data/region-2.parquet", [polygon_b])

    centroids = list(read_polygon_centroids(tmp_path))

    assert sorted(c.shard_relative_path for c in centroids) == [
        "data/region-1.parquet",
        "data/region-2.parquet",
    ]
    assert all(c.row_index == 0 for c in centroids)


def test_read_polygon_centroids_prunes_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    polygon = Polygon([(4.0, 50.0), (4.1, 50.0), (4.1, 50.1), (4.0, 50.1)])
    _build_shard(tmp_path, "data/region-1.parquet", [polygon])

    seen_columns: list[list[str]] = []
    real_iter = pq.ParquetFile.iter_batches

    def trap_iter_batches(
        self: pq.ParquetFile,
        batch_size: int = 65536,
        columns: list[str] | None = None,
        **kwargs: object,
    ) -> object:
        if columns is not None:
            seen_columns.append(list(columns))
        return real_iter(self, batch_size=batch_size, columns=columns, **kwargs)

    monkeypatch.setattr(pq.ParquetFile, "iter_batches", trap_iter_batches)
    list(read_polygon_centroids(tmp_path))

    assert seen_columns, "iter_batches must request columns"
    matched = [cols for cols in seen_columns if cols == ["geometry", "geometry_type"]]
    message = f"expected column pruning columns, got {seen_columns!r}"
    assert matched, message


def test_read_polygon_centroids_fails_closed_on_malformed_wkb(tmp_path: Path) -> None:
    output = tmp_path / "data" / "region-1.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "geometry": [b"not-valid-wkb-bytes", b"not-valid-wkb-bytes"],
            "geometry_type": ["Polygon", "Polygon"],
        }
    )
    pq.write_table(table, output, compression="zstd")
    manifest = Manifest(
        manifest_schema_version=1,
        processing_contract_version=PROCESSING_CONTRACT_VERSION,
        dataset_schema_version=DATASET_SCHEMA_VERSION,
        source=SourceIdentity("x.osm.pbf", 1, 1, "d" * 64),
        output=OutputIdentity(
            "data/region-1.parquet",
            output.stat().st_size,
            file_sha256(output),
            2,
        ),
        osmium_version="test",
        counts=RunCounts(2, {}),
    )
    write_manifest(manifest, tmp_path / "manifests" / "region-1.manifest.json")

    with pytest.raises(GeographicMapError) as excinfo:
        list(read_polygon_centroids(tmp_path))
    message = str(excinfo.value)
    assert "data/region-1.parquet" in message
    assert "row" in message.lower()


def test_iter_polygon_geometry_rejects_nonpositive_batch_size(tmp_path: Path) -> None:
    with pytest.raises(GeographicMapError, match="batch_size must be positive"):
        list(iter_polygon_geometry(tmp_path / "missing.parquet", batch_size=0))


@pytest.mark.parametrize("missing_column", ["geometry", "geometry_type"])
def test_iter_polygon_geometry_rejects_missing_required_column(
    tmp_path: Path, missing_column: str
) -> None:
    columns: dict[str, object] = {
        "geometry": [to_wkb(Polygon([(0, 0), (1, 0), (1, 1)]))],
        "geometry_type": ["Polygon"],
    }
    del columns[missing_column]
    output = tmp_path / "missing-column.parquet"
    pq.write_table(pa.table(columns), output)

    with pytest.raises(GeographicMapError, match="missing required columns"):
        list(iter_polygon_geometry(output))


def test_iter_polygon_geometry_rejects_null_geometry(tmp_path: Path) -> None:
    output = tmp_path / "null-geometry.parquet"
    pq.write_table(
        pa.table(
            {
                "geometry": pa.array([None], type=pa.binary()),
                "geometry_type": ["Polygon"],
            }
        ),
        output,
    )

    with pytest.raises(GeographicMapError, match="geometry is null"):
        list(iter_polygon_geometry(output))


def test_iter_polygon_geometry_rejects_null_geometry_type(tmp_path: Path) -> None:
    output = tmp_path / "null-geometry-type.parquet"
    pq.write_table(
        pa.table(
            {
                "geometry": [to_wkb(Polygon([(0, 0), (1, 0), (1, 1)]))],
                "geometry_type": pa.array([None], type=pa.string()),
            }
        ),
        output,
    )

    with pytest.raises(GeographicMapError, match="geometry_type is null"):
        list(iter_polygon_geometry(output))


def test_read_shard_polygon_centroids_rejects_non_polygon_type(tmp_path: Path) -> None:
    output = tmp_path / "wrong-geometry-type.parquet"
    pq.write_table(
        pa.table(
            {
                "geometry": [to_wkb(Polygon([(0, 0), (1, 0), (1, 1)]))],
                "geometry_type": ["LineString"],
            }
        ),
        output,
    )

    with pytest.raises(GeographicMapError, match="Polygon or MultiPolygon"):
        list(read_shard_polygon_centroids(output, "data/wrong-geometry-type.parquet"))


def test_iter_polygon_geometry_keeps_row_indices_across_batches(tmp_path: Path) -> None:
    polygons = [
        Polygon(
            [
                (4.0 + index, 50.0),
                (4.1 + index, 50.0),
                (4.1 + index, 50.1),
                (4.0 + index, 50.1),
            ]
        )
        for index in range(3)
    ]
    output = _write_polygon_shard(tmp_path, "data/region-1.parquet", polygons)

    rows = list(iter_polygon_geometry(output, batch_size=2))

    assert [row_index for row_index, _wkb, _geometry_type in rows] == [0, 1, 2]
