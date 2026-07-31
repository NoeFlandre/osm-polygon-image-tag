"""Tests for the dataset-card geography section and publication impact."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shapely import to_wkb
from shapely.geometry import Polygon

from osm_polygon_image_tag.artifacts.geography.render import (
    GEOGRAPHIC_PNG_RELATIVE,
)
from osm_polygon_image_tag.artifacts.publication_inventory import publication_inventory
from osm_polygon_image_tag.artifacts.publication_types import HubCommit
from osm_polygon_image_tag.artifacts.reporting import generate_metadata
from osm_polygon_image_tag.artifacts.storage import write_geoparquet
from osm_polygon_image_tag.core.errors import PublicationError
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
) -> Manifest:
    output = root / relative_path
    write_geoparquet(rows, output)
    manifest = Manifest(
        manifest_schema_version=1,
        processing_contract_version=PROCESSING_CONTRACT_VERSION,
        dataset_schema_version=DATASET_SCHEMA_VERSION,
        source=SourceIdentity(
            f"region-{relative_path}.osm.pbf",
            output.stat().st_size,
            1,
            "a" * 64,
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


def test_generate_metadata_writes_geography_block_and_png(tmp_path: Path) -> None:
    _build_shard(
        tmp_path,
        "data/region-1.parquet",
        rows=[_polygon_row(index, 4.0 + 0.01 * index, 50.0) for index in range(1, 4)],
    )
    result = generate_metadata(tmp_path)

    statistics = json.loads(result.statistics_path.read_bytes())
    geography = statistics["geography"]
    assert geography["h3_resolution"] == 3
    assert geography["polygon_rows"] == 3
    assert geography["cell_count"] >= 1
    assert geography["input_shard_count"] == 1
    assert "min_cell_count" in geography
    assert "max_cell_count" in geography
    assert "input_digest" in geography

    png_path = tmp_path / GEOGRAPHIC_PNG_RELATIVE
    assert png_path.exists()
    assert png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_dataset_card_includes_geography_section(tmp_path: Path) -> None:
    _build_shard(
        tmp_path,
        "data/region-1.parquet",
        rows=[_polygon_row(index, 4.0 + 0.01 * index, 50.0) for index in range(1, 4)],
    )
    result = generate_metadata(tmp_path)
    body = result.card_path.read_text()

    assert "## Geographic coverage" in body
    assert "### OSM polygon density" in body
    assert "![Geographic OSM Polygon Density](assets/geographic_polygon_density.png)" in body
    assert "finalized `polygons` rows" in body
    assert "geometry centroid" in body
    assert "H3 resolution" in body
    assert "Overlapping Geofabrik" in body or "overlapping source-PBF" in body
    assert "logarithmic" in body.lower()
    assert "image_assets" in body


def test_generate_metadata_progress_events_include_geography_phase(tmp_path: Path) -> None:
    _build_shard(
        tmp_path,
        "data/region-1.parquet",
        rows=[_polygon_row(index, 4.0, 50.0) for index in range(1, 4)],
    )
    events: list[dict[str, object]] = []
    generate_metadata(tmp_path, progress=events.append)
    event_names = [event["event"] for event in events]
    assert "metadata_geography_started" in event_names
    assert "metadata_geography_completed" in event_names
    completed = next(event for event in events if event["event"] == "metadata_geography_completed")
    assert completed["polygon_rows"] == 3
    assert isinstance(completed["cell_count"], int)
    assert completed["cell_count"] >= 1


def test_generate_metadata_does_not_rewrite_polygon_shards(tmp_path: Path) -> None:
    _build_shard(
        tmp_path,
        "data/region-1.parquet",
        rows=[_polygon_row(index, 4.0 + 0.01 * index, 50.0) for index in range(1, 4)],
    )
    output = tmp_path / "data" / "region-1.parquet"
    before = output.read_bytes()
    generate_metadata(tmp_path)
    assert output.read_bytes() == before


def test_generate_metadata_never_opens_pbfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Metadata generation must never read a PBF file."""
    _build_shard(
        tmp_path,
        "data/region-1.parquet",
        rows=[_polygon_row(index, 4.0, 50.0) for index in range(1, 3)],
    )
    fake_pbf = tmp_path / "raw" / "region.osm.pbf"
    fake_pbf.parent.mkdir(parents=True, exist_ok=True)
    fake_pbf.write_bytes(b"FAKE_PBF_PAYLOAD")

    from pyarrow.parquet import ParquetFile as RealPF

    real_init = RealPF.__init__

    def trap(self: RealPF, path: object, *args: object, **kwargs: object) -> None:
        if isinstance(path, str | Path) and str(path).endswith(".osm.pbf"):
            raise AssertionError("PBF opened during metadata generation")
        return real_init(self, path, *args, **kwargs)

    monkeypatch.setattr(RealPF, "__init__", trap)
    generate_metadata(tmp_path)


def test_publication_inventory_includes_geographic_png(tmp_path: Path) -> None:
    _build_shard(
        tmp_path,
        "data/region-1.parquet",
        rows=[_polygon_row(index, 4.0 + 0.01 * index, 50.0) for index in range(1, 3)],
    )
    generate_metadata(tmp_path)
    inventory = publication_inventory(tmp_path)
    rels = {item.remote_path for item in inventory}
    assert GEOGRAPHIC_PNG_RELATIVE in rels


def test_publication_inventory_rejects_corrupt_geographic_png(tmp_path: Path) -> None:
    _build_shard(
        tmp_path,
        "data/region-1.parquet",
        rows=[_polygon_row(index, 4.0, 50.0) for index in range(1, 3)],
    )
    generate_metadata(tmp_path)
    png_path = tmp_path / GEOGRAPHIC_PNG_RELATIVE
    png_path.write_bytes(b"not a PNG content")
    with pytest.raises(PublicationError, match="geographic"):
        publication_inventory(tmp_path)


def test_publication_inventory_rejects_symlinked_geographic_png(tmp_path: Path) -> None:
    _build_shard(
        tmp_path,
        "data/region-1.parquet",
        rows=[_polygon_row(index, 4.0, 50.0) for index in range(1, 3)],
    )
    generate_metadata(tmp_path)
    png_path = tmp_path / GEOGRAPHIC_PNG_RELATIVE
    target = tmp_path / "statistics" / "dataset-statistics.json"
    png_path.unlink()
    png_path.symlink_to(target)
    with pytest.raises(PublicationError, match="symlink"):
        publication_inventory(tmp_path)


def test_publication_inventory_rejects_missing_png(tmp_path: Path) -> None:
    _build_shard(
        tmp_path,
        "data/region-1.parquet",
        rows=[_polygon_row(index, 4.0, 50.0) for index in range(1, 3)],
    )
    generate_metadata(tmp_path)
    (tmp_path / GEOGRAPHIC_PNG_RELATIVE).unlink()
    with pytest.raises(PublicationError, match="geographic"):
        publication_inventory(tmp_path)


def test_publication_receipt_includes_geographic_png(tmp_path: Path) -> None:
    from osm_polygon_image_tag.artifacts.publication import (
        EXPECTED_REPO,
        publish_dataset,
    )

    _build_shard(
        tmp_path,
        "data/region-1.parquet",
        rows=[_polygon_row(index, 4.0, 50.0) for index in range(1, 3)],
    )
    generate_metadata(tmp_path)

    downloaded: list[str] = []

    class _FakeHub:
        def __init__(self) -> None:
            self.commits: list[HubCommit] = []
            self.remote: dict[str, bytes] = {}

        def commit(self, commit: HubCommit) -> str:
            self.commits.append(commit)
            for path in commit.deletions:
                self.remote.pop(path, None)
            self.remote.update(
                {item.remote_path: item.local_path.read_bytes() for item in commit.files}
            )
            return f"commit-{len(self.commits)}"

        def download(self, repo_id: str, remote_path: str, revision: str) -> bytes:
            downloaded.append(remote_path)
            assert repo_id == EXPECTED_REPO
            return self.remote[remote_path]

    hub = _FakeHub()
    publish_dataset(tmp_path, confirm_repo=EXPECTED_REPO, hub=hub)
    receipt = json.loads((tmp_path / "receipts" / "publication.json").read_text())
    receipt_paths = {entry["path"] for entry in receipt["files"]}
    assert GEOGRAPHIC_PNG_RELATIVE in receipt_paths
    assert GEOGRAPHIC_PNG_RELATIVE in downloaded


def test_publication_skips_second_run_when_inventory_unchanged(tmp_path: Path) -> None:
    """An unchanged second publication must still skip when the PNG is unchanged."""
    from osm_polygon_image_tag.artifacts.publication import (
        EXPECTED_REPO,
        publish_dataset,
    )

    _build_shard(
        tmp_path,
        "data/region-1.parquet",
        rows=[_polygon_row(index, 4.0, 50.0) for index in range(1, 3)],
    )
    generate_metadata(tmp_path)

    class _Hub:
        def __init__(self) -> None:
            self.commits: list[HubCommit] = []
            self.remote: dict[str, bytes] = {}

        def commit(self, commit: HubCommit) -> str:
            self.commits.append(commit)
            for path in commit.deletions:
                self.remote.pop(path, None)
            self.remote.update(
                {item.remote_path: item.local_path.read_bytes() for item in commit.files}
            )
            return f"commit-{len(self.commits)}"

        def download(self, repo_id: str, remote_path: str, revision: str) -> bytes:
            return self.remote[remote_path]

    hub = _Hub()
    first = publish_dataset(tmp_path, confirm_repo=EXPECTED_REPO, hub=hub)
    second = publish_dataset(tmp_path, confirm_repo=EXPECTED_REPO, hub=hub)
    assert first.status == "published"
    assert second.status == "skipped"
    assert len(hub.commits) == 1


def test_resume_through_rebuild_metadata_then_publish(tmp_path: Path) -> None:
    """A finalized shard plus an empty source directory must publish the map."""
    from osm_polygon_image_tag.artifacts.publication import (
        EXPECTED_REPO,
        publish_dataset,
    )

    _build_shard(
        tmp_path,
        "data/region-1.parquet",
        rows=[_polygon_row(index, 4.0 + 0.01 * index, 50.0) for index in range(1, 3)],
    )
    before = (tmp_path / "data" / "region-1.parquet").read_bytes()

    # Empty source directory: no PBF processing possible.
    (tmp_path / "raw").mkdir()

    result = generate_metadata(tmp_path)
    assert (tmp_path / GEOGRAPHIC_PNG_RELATIVE).exists()
    assert (tmp_path / "data" / "region-1.parquet").read_bytes() == before

    # A second metadata call must reuse the cached map.
    second = generate_metadata(tmp_path)
    assert second.card_path.read_bytes() == result.card_path.read_bytes()

    class _Hub:
        def __init__(self) -> None:
            self.commits: list[HubCommit] = []
            self.remote: dict[str, bytes] = {}

        def commit(self, commit: HubCommit) -> str:
            self.commits.append(commit)
            for path in commit.deletions:
                self.remote.pop(path, None)
            self.remote.update(
                {item.remote_path: item.local_path.read_bytes() for item in commit.files}
            )
            return f"commit-{len(self.commits)}"

        def download(self, repo_id: str, remote_path: str, revision: str) -> bytes:
            return self.remote[remote_path]

    hub = _Hub()
    publish_dataset(tmp_path, confirm_repo=EXPECTED_REPO, hub=hub)
    assert any(GEOGRAPHIC_PNG_RELATIVE in c.files for c in hub.commits) or any(
        GEOGRAPHIC_PNG_RELATIVE in [f.remote_path for f in hub.commits[0].files]
        for c in hub.commits
    )
