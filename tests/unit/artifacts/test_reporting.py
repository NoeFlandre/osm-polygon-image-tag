import json
from pathlib import Path
from typing import Any

import pytest
import yaml

import osm_polygon_image_tag.artifacts.reporting as reporting
from osm_polygon_image_tag.artifacts.manifest_inventory import verified_manifests
from osm_polygon_image_tag.artifacts.reporting import generate_metadata
from osm_polygon_image_tag.artifacts.storage import write_geoparquet
from osm_polygon_image_tag.assets.manifest import (
    ASSET_MANIFEST_SCHEMA_VERSION,
    AssetManifest,
    AssetRunCounts,
    AssetSourceIdentity,
    ResolutionSnapshotIdentity,
    write_asset_manifest,
)
from osm_polygon_image_tag.assets.schema import (
    ASSET_SCHEMA_VERSION,
    RESOLVER_CONTRACT_VERSION,
)
from osm_polygon_image_tag.assets.storage import write_asset_parquet
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
    assert b"finalized manifests and their size-checked GeoParquet and asset shards" in first_card
    assert b"cryptographically verified" not in first_card
    assert statistics["assets"] == {
        "asset_schema_versions": {},
        "cache_hits": 0,
        "direct_urls": 0,
        "duplicate_assets": 0,
        "expiring_urls": 0,
        "licensed_assets": 0,
        "network_resolutions": 0,
        "output_bytes": 0,
        "page_urls": 0,
        "pending_retries": 0,
        "provider_counts": {},
        "resolver_contract_versions": {},
        "rows": 0,
        "shards": 0,
        "status_counts": {},
        "stable_direct_urls": 0,
        "truncated_categories": 0,
    }
    frontmatter = yaml.safe_load(first_card.split(b"---", maxsplit=2)[1])
    assert frontmatter["configs"] == [
        {
            "config_name": "polygons",
            "default": True,
            "data_files": [{"split": "train", "path": "data/*.parquet"}],
        },
        {
            "config_name": "image_assets",
            "data_files": [{"split": "train", "path": "assets/*.assets.parquet"}],
        },
    ]
    assert b"category membership does not prove depiction" in first_card
    assert b"`osm_type`, `osm_id`, `osm_version`, and `source_pbf`" in first_card


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
        return verified_manifests(data_root, progress=progress)

    monkeypatch.setattr(reporting, "verified_manifests", counted)

    generate_metadata(tmp_path, progress=events.append)

    assert calls == 1
    assert [event["event"] for event in events] == [
        "metadata_manifest_scan_started",
        "metadata_manifest_scan_completed",
        "metadata_asset_manifest_scan_started",
        "metadata_asset_manifest_scan_completed",
        "metadata_catalog_sync_started",
        "metadata_catalog_sync_completed",
        "metadata_asset_catalog_sync_started",
        "metadata_asset_catalog_sync_completed",
        "metadata_statistics_started",
        "metadata_statistics_completed",
        "metadata_write_started",
        "metadata_write_completed",
    ]
    assert events[1]["manifest_count"] == 0
    assert events[3]["manifest_count"] == 0
    assert events[5]["active_shards"] == 0
    assert events[7]["active_shards"] == 0


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
    manifests = verified_manifests(tmp_path)

    assert manifests == [(manifest, data.resolve())]


def _asset_row(status: str, *, image_url: str | None) -> dict[str, object]:
    return {
        "source_pbf": "region.osm.pbf",
        "source_polygon_shard": "data/region.parquet",
        "osm_type": "way",
        "osm_id": 1,
        "osm_version": 1,
        "provider": "panoramax",
        "source_tag_key": "panoramax",
        "source_tag_value": "id",
        "canonical_reference": "id",
        "provider_asset_id": "id",
        "asset_index": 0,
        "relation_kind": "direct_reference",
        "page_url": "https://viewer.test/id",
        "image_url": image_url,
        "thumbnail_url": None,
        "image_url_expires_at": None,
        "mime_type": "image/jpeg" if image_url else None,
        "width": 100 if image_url else None,
        "height": 50 if image_url else None,
        "license_id": "CC0" if image_url else None,
        "license_url": None,
        "author": None,
        "status": status,
        "reason": None,
        "category_truncated": False,
        "retry_after": None,
        "resolver_contract_version": 1,
        "response_sha256": "d" * 64,
    }


def test_metadata_derives_factual_asset_statistics(tmp_path: Path) -> None:
    output = tmp_path / "assets" / "region.assets.parquet"
    write = write_asset_parquet(
        [
            _asset_row("resolved", image_url="https://cdn.test/image.jpg"),
            _asset_row("resolved_page_only", image_url=None),
        ],
        output,
    )
    manifest = AssetManifest(
        ASSET_MANIFEST_SCHEMA_VERSION,
        ASSET_SCHEMA_VERSION,
        RESOLVER_CONTRACT_VERSION,
        AssetSourceIdentity("data/region.parquet", 1, "a" * 64, 1),
        ResolutionSnapshotIdentity(1, "b" * 64),
        OutputIdentity(
            "assets/region.assets.parquet",
            write.size_bytes,
            file_sha256(output),
            write.row_count,
        ),
        AssetRunCounts(
            2,
            {"resolved": 1, "resolved_page_only": 1},
            {"panoramax": 2},
            0,
            0,
            1,
            3,
            4,
        ),
    )
    write_asset_manifest(
        manifest,
        tmp_path / "asset-manifests" / "region.assets.manifest.json",
    )

    result = generate_metadata(tmp_path)
    statistics = json.loads(result.statistics_path.read_bytes())

    assert statistics["assets"]["shards"] == 1
    assert statistics["assets"]["rows"] == 2
    assert statistics["assets"]["provider_counts"] == {"panoramax": 2}
    assert statistics["assets"]["status_counts"] == {
        "resolved": 1,
        "resolved_page_only": 1,
    }
    assert statistics["assets"]["direct_urls"] == 1
    assert statistics["assets"]["stable_direct_urls"] == 1
    assert statistics["assets"]["page_urls"] == 2
    assert statistics["assets"]["licensed_assets"] == 1
    assert statistics["assets"]["cache_hits"] == 3
    assert statistics["assets"]["network_resolutions"] == 4
    assert b"Resolution cache hits: 3" in result.card_path.read_bytes()
    assert b"Provider resolver requests: 4" in result.card_path.read_bytes()
