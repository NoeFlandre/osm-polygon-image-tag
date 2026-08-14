import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

import osm_polygon_image_tag.artifacts.reporting as reporting
from osm_polygon_image_tag.artifacts.asset_statistics import asset_statistics
from osm_polygon_image_tag.artifacts.dataset_card import dataset_card
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
    assert b"does not establish copyright" in first_card
    assert b"finalized, size-checked public" in first_card
    assert b"cryptographically verified" not in first_card
    assert statistics["assets"] == {
        "asset_schema_versions": {},
        "cache_hits": 0,
        "direct_urls": 0,
        "duplicate_assets": 0,
        "duplicate_assets_removed": 0,
        "duplicate_images_removed": 0,
        "duplicate_links_removed": 0,
        "expiring_urls": 0,
        "image_relation_counts": {
            "category_membership": 0,
            "direct_reference": 0,
        },
        "licensed_assets": 0,
        "network_resolutions": 0,
        "orphan_rows": 0,
        "output_bytes": (
            (tmp_path / "public/images.parquet").stat().st_size
            + (tmp_path / "public/polygon_images.parquet").stat().st_size
        ),
        "page_urls": 0,
        "pending_retries": 0,
        "provider_counts": {},
        "relationship_rows": 0,
        "resolver_contract_versions": {},
        "rows": 0,
        "shards": 0,
        "status_counts": {},
        "stable_direct_urls": 0,
        "truncated_categories": 0,
        "usable_relationship_rows": 0,
    }
    frontmatter = yaml.safe_load(first_card.split(b"---", maxsplit=2)[1])
    assert frontmatter["configs"] == [
        {
            "config_name": "polygons",
            "default": True,
            "data_files": [{"split": "train", "path": "public/polygons.parquet"}],
        },
        {
            "config_name": "images",
            "data_files": [{"split": "train", "path": "public/images.parquet"}],
        },
        {
            "config_name": "polygon_images",
            "data_files": [{"split": "train", "path": "public/polygon_images.parquet"}],
        },
    ]
    assert b"category can contain" in first_card
    assert b"same OSM type, ID, and version" in first_card
    assert b"### How repeated rows are removed" in first_card
    assert b"No finalized row is available in this snapshot." in first_card
    assert b"## Source, license, and citation" in first_card
    assert "Noé Flandre".encode() in first_card
    assert b"citation.cff" in first_card
    assert b"The provider terms and image license are separate from ODbL" in first_card
    assert b"license_id" in first_card
    assert b"not permission to copy, redistribute, or use the image" in first_card
    assert b"does not download or relicense image files" in first_card
    assert b"A tag value is the original reference." in first_card
    assert b"it is not always an image URL." in first_card
    assert b"Use `polygons` for the original OSM tags" in first_card
    assert b"one feature can have many images" in first_card


def test_metadata_syncs_packaged_hero(tmp_path: Path) -> None:
    result = generate_metadata(tmp_path)
    hero = tmp_path / "assets/hero.png"

    assert hero.is_file()
    assert file_sha256(hero) == "e36f4c54fe8c71f7df2574852b082a294ec66d3077aec2086451acd0f6a3a0bb"
    body = result.card_path.read_bytes().split(b"---", maxsplit=2)[2]
    assert body.startswith(
        b"\n![OSM Polygon Image Tag hero](assets/hero.png)\n\n# OSM Polygon Image Tag\n"
    )

    hero.write_bytes(b"stale")
    generate_metadata(tmp_path)

    assert file_sha256(hero) == "e36f4c54fe8c71f7df2574852b082a294ec66d3077aec2086451acd0f6a3a0bb"


def test_metadata_syncs_packaged_citation(tmp_path: Path) -> None:
    result = generate_metadata(tmp_path)
    citation = tmp_path / "citation.cff"

    assert citation.is_file()
    first = citation.read_bytes()
    assert first.startswith(b"cff-version: 1.2.0\n")
    assert b"repository-code: https://github.com/NoeFlandre/osm-polygon-image-tag" in first
    assert b"`citation.cff`](citation.cff)" in result.card_path.read_bytes()

    citation.write_bytes(b"stale")
    generate_metadata(tmp_path)

    assert citation.read_bytes() == first


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
        "metadata_statistics_started",
        "metadata_statistics_completed",
        "metadata_geography_started",
        "metadata_geography_completed",
        "metadata_write_started",
        "metadata_write_completed",
    ]
    assert events[1]["manifest_count"] == 0
    assert events[3]["manifest_count"] == 0


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


def test_manifest_inventory_rejects_symlinked_output(tmp_path: Path) -> None:
    output = tmp_path / "data" / "region.parquet"
    write_geoparquet([], output)
    write_manifest(
        Manifest(
            manifest_schema_version=1,
            processing_contract_version=PROCESSING_CONTRACT_VERSION,
            dataset_schema_version=DATASET_SCHEMA_VERSION,
            source=SourceIdentity("region.osm.pbf", 1, 1, "a" * 64),
            output=OutputIdentity(
                "data/region.parquet",
                output.stat().st_size,
                file_sha256(output),
                0,
            ),
            osmium_version="test",
            counts=RunCounts(0, {}),
        ),
        tmp_path / "manifests" / "region.manifest.json",
    )
    external = tmp_path / "external.parquet"
    external.write_bytes(output.read_bytes())
    output.unlink()
    output.symlink_to(external)

    with pytest.raises(ValueError, match="symlink"):
        verified_manifests(tmp_path)


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


def test_asset_statistics_separates_direct_and_indirect_image_rows(tmp_path: Path) -> None:
    rows = [
        _asset_row("resolved", image_url="https://cdn.test/direct.jpg"),
        _asset_row("resolved", image_url="https://cdn.test/category.jpg"),
        _asset_row("resolved_page_only", image_url=None),
    ]
    rows[1]["relation_kind"] = "category_membership"
    rows[1]["source_tag_value"] = "Category:Example"
    output = tmp_path / "assets" / "region.assets.parquet"
    write = write_asset_parquet(rows, output)
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
        AssetRunCounts(3, {"resolved": 2, "resolved_page_only": 1}, {"image": 3}, 0, 0, 2),
    )
    with sqlite3.connect(tmp_path / "catalog.sqlite") as connection:
        connection.execute(
            """
            CREATE TABLE asset_observations (
                shard TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                canonical_reference TEXT NOT NULL,
                provider_asset_id TEXT,
                image_url TEXT,
                page_url TEXT,
                expires_at TEXT,
                license_id TEXT,
                category_truncated INTEGER NOT NULL,
                retry_after TEXT,
                resolver_contract_version INTEGER NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO asset_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "public/images.parquet",
                    row["provider"],
                    row["status"],
                    row["canonical_reference"],
                    row["provider_asset_id"],
                    row["image_url"],
                    row["page_url"],
                    None,
                    row["license_id"],
                    int(row["category_truncated"] is True),
                    None,
                    row["resolver_contract_version"],
                )
                for row in rows
            ],
        )
    statistics = asset_statistics(tmp_path / "catalog.sqlite", [(manifest, output)])

    assert statistics["direct_urls"] == 2
    assert statistics["image_relation_counts"] == {
        "category_membership": 1,
        "direct_reference": 1,
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
    assert statistics["assets"]["rows"] == 0
    assert statistics["assets"]["provider_counts"] == {}
    assert statistics["assets"]["status_counts"] == {}
    assert statistics["assets"]["direct_urls"] == 0
    assert statistics["assets"]["stable_direct_urls"] == 0
    assert statistics["assets"]["page_urls"] == 0
    assert statistics["assets"]["licensed_assets"] == 0
    assert statistics["assets"]["cache_hits"] == 3
    assert statistics["assets"]["network_resolutions"] == 4
    assert statistics["assets"]["orphan_rows"] == 2
    assert b"Cached lookups reused: 3" in result.card_path.read_bytes()
    assert b"New provider lookups: 4" in result.card_path.read_bytes()
    assert b"Unique image records: 0" in result.card_path.read_bytes()


def test_dataset_card_formats_counts_and_explains_examples() -> None:
    statistics: dict[str, Any] = {
        "provider_counts": {"image": 2_555_555},
        "shards": 12_345,
        "rows": 2_555_555,
        "duplicate_observations": 123_456,
        "assets": {
            "rows": 2_555_555,
            "direct_urls": 2_555_555,
            "image_relation_counts": {
                "category_membership": 555_555,
                "direct_reference": 2_000_000,
            },
            "usable_relationship_rows": 2_555_555,
            "stable_direct_urls": 1_999_999,
            "page_urls": 2_400_000,
            "cache_hits": 8_888_888,
            "network_resolutions": 7_777_777,
        },
        "geography": {
            "h3_resolution": 3,
            "cell_count": 123_456,
            "min_cell_count": 1,
            "max_cell_count": 2_555_555,
            "input_shard_count": 12_345,
        },
    }
    card = dataset_card(
        statistics,
        examples={
            "polygon": {"osm_id": 42},
            "image": {"image_url": "https://example.test/x?token=secret"},
            "polygon_image": {"image_id": "img_42"},
        },
    ).decode()

    assert "Published OSM features: 2,555,555" in card
    assert "New provider lookups: 7,777,777" in card
    assert "Among those usable links:" in card
    assert "The source-tag counts below are counts of polygons carrying each tag, not image" in card
    assert "The percentages below use links whose image record has a usable image URL." in card
    assert "Directly linked from an OSM tag: 2,000,000 (78.3%)" in card
    assert "Indirectly reached through a Wikimedia Commons category: 555,555 (21.7%)" in card
    assert "one row per OSM type and ID" in card
    assert "We keep one copy" in card
    assert '"osm_id": 42' in card
    assert '"image_url": "https://example.test/x?token=[redacted]"' in card
    assert "token=secret" not in card
