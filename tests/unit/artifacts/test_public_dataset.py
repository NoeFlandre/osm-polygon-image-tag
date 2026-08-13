from __future__ import annotations

import json
from pathlib import Path

from shapely import to_wkb
from shapely.geometry import Polygon

from osm_polygon_image_tag.artifacts.public_dataset import build_public_dataset
from osm_polygon_image_tag.artifacts.storage import write_geoparquet
from osm_polygon_image_tag.assets.manifest import (
    ASSET_MANIFEST_SCHEMA_VERSION,
    AssetManifest,
    AssetRunCounts,
    AssetSourceIdentity,
    ResolutionSnapshotIdentity,
    write_asset_manifest,
)
from osm_polygon_image_tag.assets.schema import ASSET_SCHEMA_VERSION, RESOLVER_CONTRACT_VERSION
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


def _polygon_row(
    source: str, *, osm_id: int = 1, image: str = "https://example.test/a.jpg"
) -> dict[str, object]:
    polygon = Polygon([(4, 50), (4.01, 50), (4.01, 50.01), (4, 50.01)])
    return {
        "osm_type": "way",
        "osm_id": osm_id,
        "osm_version": 1,
        "osm_changeset": 1,
        "osm_timestamp": None,
        "source_pbf": source,
        "source_feature_id": f"{source}|way|{osm_id}|1",
        "geometry": to_wkb(polygon),
        "geometry_type": "Polygon",
        "area_m2": 1.0,
        "bbox_min_lon": 4.0,
        "bbox_min_lat": 50.0,
        "bbox_max_lon": 4.01,
        "bbox_max_lat": 50.01,
        "tags": {"image": image, "name": "Example"},
        "image": image,
        "wikimedia_commons": None,
        "mapillary": None,
        "panoramax": None,
        "panoramax_values": {},
        "kartaview": None,
        "flickr": None,
        "bubbleid": None,
    }


def _asset_row(source: str, *, source_shard: str = "data/source.parquet") -> dict[str, object]:
    return {
        "source_pbf": source,
        "source_polygon_shard": source_shard,
        "osm_type": "way",
        "osm_id": 1,
        "osm_version": 1,
        "provider": "image",
        "source_tag_key": "image",
        "source_tag_value": "https://example.test/a.jpg",
        "canonical_reference": "https://example.test/a.jpg",
        "provider_asset_id": "a",
        "asset_index": 0,
        "relation_kind": "direct_reference",
        "page_url": "https://example.test/a.jpg",
        "image_url": "https://example.test/a.jpg",
        "thumbnail_url": None,
        "image_url_expires_at": None,
        "mime_type": "image/jpeg",
        "width": 100,
        "height": 100,
        "license_id": None,
        "license_url": None,
        "author": None,
        "status": "resolved",
        "reason": None,
        "category_truncated": False,
        "retry_after": None,
        "resolver_contract_version": 1,
        "response_sha256": "a" * 64,
    }


def _write_polygon_manifest(root: Path, source: str, rows: list[dict[str, object]]) -> None:
    output = root / "data" / f"{Path(source).stem}.parquet"
    write_geoparquet(rows, output)
    write_manifest(
        Manifest(
            1,
            PROCESSING_CONTRACT_VERSION,
            DATASET_SCHEMA_VERSION,
            SourceIdentity(source, 1, 1, "b" * 64),
            OutputIdentity(
                output.relative_to(root).as_posix(),
                output.stat().st_size,
                file_sha256(output),
                len(rows),
            ),
            "test",
            RunCounts(len(rows), {}),
        ),
        root / "manifests" / f"{Path(source).stem}.manifest.json",
    )


def _write_asset_manifest(
    root: Path, source: str, source_shard: str, rows: list[dict[str, object]]
) -> None:
    output = root / "assets" / f"{Path(source).stem}.assets.parquet"
    write = write_asset_parquet(rows, output)
    write_asset_manifest(
        AssetManifest(
            ASSET_MANIFEST_SCHEMA_VERSION,
            ASSET_SCHEMA_VERSION,
            RESOLVER_CONTRACT_VERSION,
            AssetSourceIdentity(source_shard, 1, "c" * 64, len(rows)),
            ResolutionSnapshotIdentity(1, "d" * 64),
            OutputIdentity(
                output.relative_to(root).as_posix(),
                write.size_bytes,
                file_sha256(output),
                write.row_count,
            ),
            AssetRunCounts(
                len(rows), {"resolved": len(rows)}, {"image": len(rows)}, 0, 0, len(rows)
            ),
        ),
        root / "asset-manifests" / f"{Path(source).stem}.assets.manifest.json",
    )


def test_public_dataset_deduplicates_identity_and_preserves_provenance(tmp_path: Path) -> None:
    _write_polygon_manifest(tmp_path, "z-region.osm.pbf", [_polygon_row("z-region.osm.pbf")])
    _write_polygon_manifest(tmp_path, "a-region.osm.pbf", [_polygon_row("a-region.osm.pbf")])
    _write_asset_manifest(
        tmp_path,
        "z-region.osm.pbf",
        "data/z-region.parquet",
        [_asset_row("z-region.osm.pbf", source_shard="data/z-region.parquet")],
    )
    _write_asset_manifest(
        tmp_path,
        "a-region.osm.pbf",
        "data/a-region.parquet",
        [_asset_row("a-region.osm.pbf", source_shard="data/a-region.parquet")],
    )

    result = build_public_dataset(tmp_path)
    assert result.polygon_rows == 1
    assert result.duplicate_polygon_rows == 1
    assert result.asset_rows == 1
    assert result.duplicate_asset_rows == 1

    import pyarrow.parquet as pq

    polygon = pq.read_table(result.polygon_path).to_pylist()[0]
    asset = pq.read_table(result.asset_path).to_pylist()[0]
    assert polygon["source_pbf"] == "a-region.osm.pbf"
    assert polygon["source_pbfs"] == ["a-region.osm.pbf", "z-region.osm.pbf"]
    assert asset["source_pbf"] == "a-region.osm.pbf"
    assert asset["source_polygon_shard"] == "public/polygons.parquet"

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["polygon_rows"] == 1
    assert manifest["duplicate_polygon_rows"] == 1


def test_public_dataset_is_reused_when_inputs_are_unchanged(tmp_path: Path) -> None:
    _write_polygon_manifest(tmp_path, "region.osm.pbf", [_polygon_row("region.osm.pbf")])
    first = build_public_dataset(tmp_path)
    polygon_before = first.polygon_path.read_bytes()
    second = build_public_dataset(tmp_path)

    assert second.reused is True
    assert second.polygon_path.read_bytes() == polygon_before
