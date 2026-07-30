import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import osm_polygon_image_tag.artifacts.reporting as reporting
from osm_polygon_image_tag.artifacts.catalog import verified_manifests as catalog_verified_manifests
from osm_polygon_image_tag.artifacts.reporting import generate_metadata
from osm_polygon_image_tag.artifacts.storage import write_geoparquet
from osm_polygon_image_tag.core.config import PipelinePaths
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
from osm_polygon_image_tag.core.progress import Progress
from osm_polygon_image_tag.ingest.discovery import discover_pbfs
from osm_polygon_image_tag.runtime.pipeline import build_one

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
        "bubbleid": 0,
        "flickr": 0,
        "image": 0,
        "kartaview": 0,
        "mapillary": 0,
        "panoramax": 0,
        "wikimedia_commons": 0,
    }
    assert b"Open Database License" in first_card
    assert b"does not establish image copyright" in first_card


def test_metadata_reports_detailed_progress_and_scans_manifests_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[dict[str, object]] = []
    calls = 0

    def counted(
        data_root: Path, *, progress: Progress | None = None
    ) -> list[tuple[Manifest, Path]]:
        nonlocal calls
        calls += 1
        return catalog_verified_manifests(data_root, progress=progress)

    monkeypatch.setattr(reporting, "verified_manifests", counted)

    generate_metadata(tmp_path, progress=events.append)

    assert calls == 1
    assert [event["event"] for event in events] == [
        "metadata_manifest_scan_started",
        "metadata_manifest_scan_completed",
        "metadata_catalog_sync_started",
        "metadata_catalog_sync_completed",
        "metadata_statistics_started",
        "metadata_statistics_completed",
        "metadata_write_started",
        "metadata_write_completed",
    ]
    assert events[1]["manifest_count"] == 0
    assert events[3]["active_shards"] == 0


def test_metadata_reuses_manifest_digest_without_rehashing_parquet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data" / "region.parquet"
    write_geoparquet([], data)
    manifest = Manifest(
        manifest_schema_version=1,
        processing_contract_version=PROCESSING_CONTRACT_VERSION,
        dataset_schema_version=DATASET_SCHEMA_VERSION,
        source=SourceIdentity("region.osm.pbf", 1, 1, "a" * 64),
        output=OutputIdentity("data/region.parquet", data.stat().st_size, file_sha256(data), 0),
        osmium_version="test",
        counts=RunCounts(0, {}),
    )
    write_manifest(manifest, tmp_path / "manifests" / "region.manifest.json")

    original_open: Any = Path.open

    def reject_python_read(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == data and (not args or args[0] == "rb"):
            raise AssertionError("Parquet was rehashed")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_python_read)
    monkeypatch.setattr(
        "osm_polygon_image_tag.artifacts.catalog.pq.ParquetFile",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("finalized Parquet was structurally revalidated")
        ),
    )

    manifests = catalog_verified_manifests(tmp_path)

    assert manifests == [(manifest, data.resolve())]


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
