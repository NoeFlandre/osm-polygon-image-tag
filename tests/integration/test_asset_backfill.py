from dataclasses import asdict
from pathlib import Path

import pytest
from shapely import to_wkb
from shapely.geometry import Polygon

from osm_polygon_image_tag.artifacts.storage import write_geoparquet
from osm_polygon_image_tag.assets.builder import build_asset_shard
from osm_polygon_image_tag.assets.cache import ResolutionCache, ResolutionRecord
from osm_polygon_image_tag.assets.references import SourceReference
from osm_polygon_image_tag.core.manifest import (
    DATASET_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    PROCESSING_CONTRACT_VERSION,
    Manifest,
    OutputIdentity,
    RunCounts,
    SourceIdentity,
    file_sha256,
)
from osm_polygon_image_tag.ingest.extraction import ExportRecord
from osm_polygon_image_tag.ingest.transform import AcceptedRow, transform_record
from osm_polygon_image_tag.resolvers.types import (
    ResolutionResult,
    ResolvedAsset,
)


class Registry:
    async def resolve_reference(
        self,
        reference: SourceReference,
        *,
        bbox: tuple[float, float, float, float],
        resolver_contract_version: int,
    ) -> ResolutionRecord:
        result = ResolutionResult(
            status="resolved",
            assets=(ResolvedAsset(image_url="https://cdn.test/image.jpg"),),
        )
        return ResolutionRecord(
            reference.provider,
            reference.canonical_reference,
            resolver_contract_version,
            result.status,
            tuple(asdict(asset) for asset in result.assets),
            result.retry_after,
        )


@pytest.mark.asyncio
async def test_historical_asset_backfill_does_not_need_or_modify_pbf(tmp_path: Path) -> None:
    data_root = tmp_path / "generated"
    polygon_path = data_root / "data" / "region.parquet"
    record = ExportRecord(
        geometry_ewkb_hex=to_wkb(
            Polygon([(4.35, 50.84), (4.37, 50.84), (4.37, 50.86), (4.35, 50.86)]),
            hex=True,
        ),
        osm_type="way",
        osm_id=7,
        version=2,
        changeset=3,
        timestamp=None,
        tags={"image": "https://example.test/image.jpg"},
    )
    outcome = transform_record(record, source_pbf="missing/region.osm.pbf")
    assert isinstance(outcome, AcceptedRow)
    write_geoparquet([outcome.values], polygon_path)
    polygon_manifest = Manifest(
        MANIFEST_SCHEMA_VERSION,
        PROCESSING_CONTRACT_VERSION,
        DATASET_SCHEMA_VERSION,
        SourceIdentity("missing/region.osm.pbf", 1, 1, "a" * 64),
        OutputIdentity(
            "data/region.parquet",
            polygon_path.stat().st_size,
            file_sha256(polygon_path),
            1,
        ),
        "osmium historical",
        RunCounts(1, {}),
    )
    before_bytes = polygon_path.read_bytes()
    before_mtime = polygon_path.stat().st_mtime_ns
    assert not (tmp_path / "missing" / "region.osm.pbf").exists()

    with ResolutionCache.open(data_root) as cache:
        result = await build_asset_shard(
            polygon_manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=Registry(),
            stop_requested=lambda: False,
            progress=lambda _event: None,
        )

    assert result.status == "built"
    assert polygon_path.read_bytes() == before_bytes
    assert polygon_path.stat().st_mtime_ns == before_mtime
