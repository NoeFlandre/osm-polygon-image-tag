"""Tests for the geographic map pipeline (aggregation, caching, render)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shapely import to_wkb
from shapely.geometry import Polygon

from osm_polygon_image_tag.artifacts.geography.models import (
    GeographicMapError,
)
from osm_polygon_image_tag.artifacts.geography.pipeline import (
    CACHE_DIR_RELATIVE,
    CACHE_SCHEMA_VERSION,
    build_geographic_map,
)
from osm_polygon_image_tag.artifacts.geography.render import (
    GEOGRAPHIC_PNG_RELATIVE,
)
from osm_polygon_image_tag.artifacts.reporting import generate_metadata
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


def _polygon_row(index: int, lon: float, lat: float) -> dict[str, object]:
    polygon = Polygon([(lon, lat), (lon + 0.01, lat), (lon + 0.01, lat + 0.01), (lon, lat + 0.01)])
    return {
        "osm_type": "way",
        "osm_id": index,
        "osm_version": 1,
        "osm_changeset": 1,
        "osm_timestamp": None,
        "source_pbf": f"region-{index}.osm.pbf",
        "source_feature_id": f"region-{index}/way/{index}",
        "geometry": to_wkb(polygon),
        "geometry_type": "Polygon",
        "area_m2": 1.0,
        "bbox_min_lon": polygon.bounds[0],
        "bbox_min_lat": polygon.bounds[1],
        "bbox_max_lon": polygon.bounds[2],
        "bbox_max_lat": polygon.bounds[3],
        "tags": {"image": f"https://img.test/{index}.jpg"},
        "image": f"https://img.test/{index}.jpg",
        "wikimedia_commons": None,
        "mapillary": None,
        "panoramax": None,
        "panoramax_values": {},
        "kartaview": None,
        "flickr": None,
        "bubbleid": None,
    }


def _build_shard(
    root: Path,
    relative_path: str,
    *,
    rows: list[dict[str, object]],
    source_name: str | None = None,
    digest_hex: str = "a" * 64,
) -> Manifest:
    output = root / relative_path
    write_geoparquet(rows, output)
    manifest = Manifest(
        manifest_schema_version=1,
        processing_contract_version=PROCESSING_CONTRACT_VERSION,
        dataset_schema_version=DATASET_SCHEMA_VERSION,
        source=SourceIdentity(
            source_name or relative_path,
            output.stat().st_size,
            1,
            digest_hex,
        ),
        output=OutputIdentity(
            relative_path,
            output.stat().st_size,
            file_sha256(output),
            len(rows),
        ),
        osmium_version="test",
        counts=RunCounts(len(rows), {}),
    )
    write_manifest(
        manifest,
        root / "manifests" / f"{Path(relative_path).stem}.manifest.json",
    )
    return manifest


def _seed_two_shards(tmp_path: Path) -> list[Manifest]:
    rows_a = [_polygon_row(index, 4.0, 50.0) for index in range(1, 4)]
    rows_b = [_polygon_row(index, 2.0, 48.0) for index in range(4, 6)]
    return [
        _build_shard(tmp_path, "data/region-a.parquet", rows=rows_a, digest_hex="a" * 64),
        _build_shard(tmp_path, "data/region-b.parquet", rows=rows_b, digest_hex="b" * 64),
    ]


def test_build_geographic_map_aggregates_polygon_rows(tmp_path: Path) -> None:
    _seed_two_shards(tmp_path)
    result = build_geographic_map(tmp_path)
    assert result.statistics.polygon_rows == 5
    assert result.statistics.cell_count == len(result.cells)
    assert sum(cell.polygon_count for cell in result.cells) == 5
    assert result.statistics.min_cell_count >= 1
    assert result.statistics.max_cell_count >= result.statistics.min_cell_count
    assert result.statistics.input_shard_count == 2
    assert GEOGRAPHIC_PNG_RELATIVE.endswith(".png")
    png_path = tmp_path / GEOGRAPHIC_PNG_RELATIVE
    assert png_path.exists()
    assert png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_build_geographic_map_is_deterministic_across_runs(tmp_path: Path) -> None:
    _seed_two_shards(tmp_path)
    first = build_geographic_map(tmp_path)
    second = build_geographic_map(tmp_path)
    assert first.cells == second.cells
    digest_first = (tmp_path / GEOGRAPHIC_PNG_RELATIVE).read_bytes()
    digest_second = (tmp_path / GEOGRAPHIC_PNG_RELATIVE).read_bytes()
    assert digest_first == digest_second


def test_build_geographic_map_reuses_cache_for_unchanged_inputs(tmp_path: Path) -> None:
    _seed_two_shards(tmp_path)
    first = build_geographic_map(tmp_path)
    cache_dir = tmp_path / CACHE_DIR_RELATIVE
    cache_file = cache_dir / "shards.json"
    assert cache_file.is_file()
    cache_payload = json.loads(cache_file.read_text())
    assert cache_payload["schema_version"] == CACHE_SCHEMA_VERSION
    # The per-shard cache stores compact cell counts plus the finalized-shard
    # identity, not one JSON record per polygon row.
    cached_shards = cache_payload["shards"]
    total_cached_rows = sum(entry["row_count"] for entry in cached_shards.values())
    assert total_cached_rows == 5
    assert all("sha256" in entry and "cells" in entry for entry in cached_shards.values())
    assert sorted(cache_payload["shards"].keys()) == [
        "data/region-a.parquet",
        "data/region-b.parquet",
    ]

    # A second build must reuse the cached shards: the bytes must be
    # identical and the cell list must match.
    written_before = cache_file.read_bytes()
    second = build_geographic_map(tmp_path)
    assert second.cells == first.cells
    assert cache_file.read_bytes() == written_before


def test_build_geographic_map_rebuilds_only_changed_shard(tmp_path: Path) -> None:
    _seed_two_shards(tmp_path)
    first = build_geographic_map(tmp_path)
    first_cells = list(first.cells)

    # Add a third shard; the existing two must be reused.
    extra = _polygon_row(6, 13.0, 52.0)
    _build_shard(
        tmp_path,
        "data/region-c.parquet",
        rows=[extra],
        digest_hex="c" * 64,
    )
    second = build_geographic_map(tmp_path)
    assert second.statistics.input_shard_count == 3
    assert second.statistics.polygon_rows == 6
    assert sum(cell.polygon_count for cell in second.cells) == 6
    # Cells from the first two shards remain.
    first_keys = {(c.h3_cell, c.polygon_count) for c in first_cells}
    assert all(
        (c.h3_cell, c.polygon_count) in first_keys
        for c in second.cells
        if c.h3_cell in {fc.h3_cell for fc in first_cells}
    )


def test_build_geographic_map_rebuilds_when_manifest_digest_changes(tmp_path: Path) -> None:
    _seed_two_shards(tmp_path)
    build_geographic_map(tmp_path)
    cache_payload_before = json.loads((tmp_path / CACHE_DIR_RELATIVE / "pipeline.json").read_text())

    # Modify a finalized shard manifest so its digest no longer matches.
    manifest_path = tmp_path / "manifests" / "region-a.manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["output"]["sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(payload, sort_keys=True))

    # Rebuild must invalidate the cache and recompute the input digest.
    result = build_geographic_map(tmp_path)
    assert result.statistics.polygon_rows == 5
    cache_payload_after = json.loads((tmp_path / CACHE_DIR_RELATIVE / "pipeline.json").read_text())
    assert cache_payload_before["input_digest"] != cache_payload_after["input_digest"]


def test_build_geographic_map_rebuilds_same_row_count_when_shard_digest_changes(
    tmp_path: Path,
) -> None:
    _seed_two_shards(tmp_path)
    first = build_geographic_map(tmp_path)

    # Replace a finalized shard with the same number of rows but different
    # geometry. A row-count-only cache check would incorrectly reuse it.
    replacement = [_polygon_row(index, 130.0, -20.0) for index in range(1, 4)]
    _build_shard(
        tmp_path,
        "data/region-a.parquet",
        rows=replacement,
        digest_hex="f" * 64,
    )
    events: list[dict[str, object]] = []
    second = build_geographic_map(tmp_path, progress=events.append)

    assert second.cells != first.cells
    completed = next(event for event in events if event["event"] == "metadata_geography_completed")
    assert completed["reused_shard_count"] == 1
    assert completed["rebuilt_shard_count"] == 1


def test_build_geographic_map_reads_only_changed_shards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from osm_polygon_image_tag.artifacts.geography import pipeline

    _seed_two_shards(tmp_path)
    calls: list[str] = []
    real_reader = pipeline.read_shard_polygon_centroids

    def counted_reader(path: Path, relative_path: str, **kwargs: int):
        calls.append(relative_path)
        yield from real_reader(path, relative_path, **kwargs)

    monkeypatch.setattr(pipeline, "read_shard_polygon_centroids", counted_reader)
    build_geographic_map(tmp_path)
    assert sorted(calls) == ["data/region-a.parquet", "data/region-b.parquet"]

    calls.clear()
    build_geographic_map(tmp_path)
    assert calls == []

    _build_shard(
        tmp_path,
        "data/region-c.parquet",
        rows=[_polygon_row(6, 13.0, 52.0)],
        digest_hex="c" * 64,
    )
    calls.clear()
    build_geographic_map(tmp_path)
    assert calls == ["data/region-c.parquet"]


def test_build_geographic_map_rebuilds_safely_on_corrupt_cache(tmp_path: Path) -> None:
    _seed_two_shards(tmp_path)
    cache_dir = tmp_path / CACHE_DIR_RELATIVE
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "shards.json").write_text("not valid json")

    result = build_geographic_map(tmp_path)
    assert result.statistics.polygon_rows == 5
    assert (tmp_path / CACHE_DIR_RELATIVE / "shards.json").read_text()


def test_build_geographic_map_rebuilds_safely_on_corrupt_stats_cache(tmp_path: Path) -> None:
    _seed_two_shards(tmp_path)
    build_geographic_map(tmp_path)
    stats_path = tmp_path / CACHE_DIR_RELATIVE / "pipeline.json"
    payload = json.loads(stats_path.read_text())
    payload["cell_count"] = "not-an-integer"
    stats_path.write_text(json.dumps(payload))

    result = build_geographic_map(tmp_path)

    assert result.statistics.polygon_rows == 5


def test_build_geographic_map_rebuilds_when_png_missing(tmp_path: Path) -> None:
    _seed_two_shards(tmp_path)
    build_geographic_map(tmp_path)
    (tmp_path / GEOGRAPHIC_PNG_RELATIVE).unlink()
    # The function must regenerate the PNG even when the cache is intact.
    result = build_geographic_map(tmp_path)
    assert (tmp_path / GEOGRAPHIC_PNG_RELATIVE).exists()
    assert result.statistics.polygon_rows == 5


def test_build_geographic_map_rejects_incompatible_cache_schema(tmp_path: Path) -> None:
    _seed_two_shards(tmp_path)
    cache_dir = tmp_path / CACHE_DIR_RELATIVE
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "shards.json").write_text(
        json.dumps({"schema_version": CACHE_SCHEMA_VERSION - 1, "shards": []})
    )
    result = build_geographic_map(tmp_path)
    assert result.statistics.polygon_rows == 5


def test_build_geographic_map_with_no_shards_renders_empty_world(tmp_path: Path) -> None:
    (tmp_path / "manifests").mkdir(parents=True, exist_ok=True)
    result = build_geographic_map(tmp_path)
    assert result.statistics.polygon_rows == 0
    assert result.statistics.cell_count == 0
    assert (tmp_path / GEOGRAPHIC_PNG_RELATIVE).exists()
    assert (tmp_path / GEOGRAPHIC_PNG_RELATIVE).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_build_geographic_map_emits_progress_events(tmp_path: Path) -> None:
    _seed_two_shards(tmp_path)
    events: list[dict[str, object]] = []
    build_geographic_map(tmp_path, progress=events.append)
    names = [event["event"] for event in events]
    assert "metadata_geography_started" in names
    assert "metadata_geography_completed" in names
    completed = next(event for event in events if event["event"] == "metadata_geography_completed")
    assert completed["polygon_rows"] == 5
    assert isinstance(completed["cell_count"], int)
    assert completed["cell_count"] >= 1
    assert completed["input_shard_count"] == 2
    shard_event_name = "metadata_geography_shard_started"
    shard_events = [event for event in events if event["event"] == shard_event_name]
    assert len(shard_events) == 2


def test_build_geographic_map_cache_is_not_in_inventory(tmp_path: Path) -> None:
    """The private cache directory must never reach the publication inventory."""
    from osm_polygon_image_tag.artifacts.publication_inventory import publication_inventory

    _seed_two_shards(tmp_path)
    generate_metadata(tmp_path)
    inventory = publication_inventory(tmp_path)
    rels = {item.remote_path for item in inventory}
    assert all(not path.startswith("cache/") for path in rels)
    assert GEOGRAPHIC_PNG_RELATIVE in rels


def test_build_geographic_map_duplicate_observations_counted_separately(tmp_path: Path) -> None:
    """Duplicate OSM observations in separate shards must each produce a polygon row."""
    row = _polygon_row(1, 4.0, 50.0)
    _build_shard(tmp_path, "data/region-a.parquet", rows=[row], digest_hex="a" * 64)
    _build_shard(tmp_path, "data/region-b.parquet", rows=[row], digest_hex="b" * 64)
    result = build_geographic_map(tmp_path)
    assert result.statistics.polygon_rows == 2
    assert sum(cell.polygon_count for cell in result.cells) == 2


def test_build_geographic_map_fails_closed_on_malformed_wkb(tmp_path: Path) -> None:
    """The full pipeline must surface the shard and row index on malformed WKB."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    output = tmp_path / "data" / "region-bad.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "geometry": [b"not-valid-wkb"],
            "geometry_type": ["Polygon"],
        }
    )
    pq.write_table(table, output, compression="zstd")
    manifest = Manifest(
        manifest_schema_version=1,
        processing_contract_version=PROCESSING_CONTRACT_VERSION,
        dataset_schema_version=DATASET_SCHEMA_VERSION,
        source=SourceIdentity("bad.osm.pbf", 1, 1, "d" * 64),
        output=OutputIdentity(
            "data/region-bad.parquet",
            output.stat().st_size,
            file_sha256(output),
            1,
        ),
        osmium_version="test",
        counts=RunCounts(1, {}),
    )
    write_manifest(
        manifest,
        tmp_path / "manifests" / "region-bad.manifest.json",
    )

    with pytest.raises(GeographicMapError) as excinfo:
        build_geographic_map(tmp_path)
    assert "region-bad.parquet" in str(excinfo.value)


def test_build_geographic_map_progress_reports_reused_count(tmp_path: Path) -> None:
    _seed_two_shards(tmp_path)
    events: list[dict[str, object]] = []
    build_geographic_map(tmp_path, progress=events.append)
    completed = next(event for event in events if event["event"] == "metadata_geography_completed")
    assert completed["reused_shard_count"] == 0  # First run, nothing reusable yet.
    second_events: list[dict[str, object]] = []
    build_geographic_map(tmp_path, progress=second_events.append)
    second_completed = next(
        event for event in second_events if event["event"] == "metadata_geography_completed"
    )
    assert second_completed["reused_shard_count"] == 2


def test_pipeline_module_does_not_open_pbf_files(tmp_path: Path) -> None:
    """The pipeline must never reach into the source PBF tree."""
    _seed_two_shards(tmp_path)
    # Place a source PBF in an adjacent path that should never be opened.
    source = tmp_path / "raw"
    source.mkdir()
    pbf = source / "should-not-be-read.osm.pbf"
    pbf.write_bytes(b"NEVER_OPEN")

    from pyarrow import parquet as real_parquet

    original_read_table = real_parquet.read_table

    def trap_read_table(path: object, *args: object, **kwargs: object) -> object:
        if isinstance(path, str | Path) and str(path).endswith(".osm.pbf"):
            raise AssertionError("Parquet reader reached a PBF")
        return original_read_table(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(real_parquet, "read_table", trap_read_table)
    try:
        build_geographic_map(tmp_path)
    finally:
        monkeypatch.undo()
