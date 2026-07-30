import asyncio
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from shapely import to_wkb
from shapely.geometry import Polygon

from osm_polygon_image_tag.artifacts.storage import write_geoparquet
from osm_polygon_image_tag.assets.builder import build_asset_shard
from osm_polygon_image_tag.assets.cache import (
    ResolutionCache,
    ResolutionKey,
    ResolutionRecord,
)
from osm_polygon_image_tag.assets.manifest import read_asset_manifest
from osm_polygon_image_tag.assets.polygon_input import POLYGON_COLUMNS
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
    ResolverContext,
)

PANORAMAX_ID = "4492cea4-1018-4285-8074-cf3d37f3c673"


def _polygon_row(tags: dict[str, str] | None = None) -> dict[str, object]:
    record = ExportRecord(
        geometry_ewkb_hex=to_wkb(
            Polygon([(4.35, 50.84), (4.37, 50.84), (4.37, 50.86), (4.35, 50.86)]),
            hex=True,
        ),
        osm_type="way",
        osm_id=7,
        version=2,
        changeset=3,
        timestamp="2026-01-01T00:00:00Z",
        tags=tags or {"panoramax": PANORAMAX_ID, "panoramax:0": PANORAMAX_ID},
    )
    outcome = transform_record(record, source_pbf="region.osm.pbf")
    assert isinstance(outcome, AcceptedRow)
    return outcome.values


def polygon_fixture(
    tmp_path: Path, *, tags: dict[str, str] | None = None
) -> tuple[Manifest, Path, Path]:
    data_root = tmp_path / "generated"
    polygon_path = data_root / "data" / "region.parquet"
    write_geoparquet([_polygon_row(tags)], polygon_path)
    manifest = Manifest(
        manifest_schema_version=MANIFEST_SCHEMA_VERSION,
        processing_contract_version=PROCESSING_CONTRACT_VERSION,
        dataset_schema_version=DATASET_SCHEMA_VERSION,
        source=SourceIdentity("region.osm.pbf", 100, 1, "a" * 64),
        output=OutputIdentity(
            "data/region.parquet",
            polygon_path.stat().st_size,
            file_sha256(polygon_path),
            1,
        ),
        osmium_version="osmium test",
        counts=RunCounts(accepted_rows=1, rejections={}),
    )
    return manifest, polygon_path, data_root


class Resolver:
    provider = "panoramax"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ResolverContext]] = []

    async def resolve(
        self, canonical_reference: str, *, context: ResolverContext
    ) -> ResolutionResult:
        self.calls.append((canonical_reference, context))
        return ResolutionResult(
            status="resolved",
            assets=(
                ResolvedAsset(
                    provider_asset_id=canonical_reference,
                    page_url=f"https://viewer.test/{canonical_reference}",
                    image_url=f"https://cdn.test/{canonical_reference}.jpg",
                    mime_type="image/jpeg",
                ),
            ),
        )


class Registry:
    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver

    async def resolve_reference(
        self,
        reference: SourceReference,
        *,
        bbox: tuple[float, float, float, float],
        resolver_contract_version: int,
    ) -> ResolutionRecord:
        result = await self.resolver.resolve(
            reference.canonical_reference,
            context=ResolverContext(bbox=bbox, environment={}),
        )
        return ResolutionRecord(
            reference.provider,
            reference.canonical_reference,
            resolver_contract_version,
            result.status,
            tuple(asdict(asset) for asset in result.assets),
            result.retry_after,
            reason=result.reason,
            category_truncated=result.category_truncated,
        )


@pytest.mark.asyncio
async def test_builder_reads_only_needed_polygon_columns_and_deduplicates_resolution(
    tmp_path: Path,
) -> None:
    manifest, polygon_path, data_root = polygon_fixture(tmp_path)
    resolver = Resolver()

    with ResolutionCache.open(data_root) as cache:
        result = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=Registry(resolver),
            stop_requested=lambda: False,
            progress=lambda _event: None,
        )

    assert "geometry" not in POLYGON_COLUMNS
    assert "area_m2" not in POLYGON_COLUMNS
    assert result.status == "built"
    assert len(resolver.calls) == 1
    table = pq.read_table(result.asset_path)
    assert table.column("source_tag_key").to_pylist() == ["panoramax", "panoramax:0"]
    assert table.column("canonical_reference").to_pylist() == [PANORAMAX_ID, PANORAMAX_ID]
    assert table.column("image_url").to_pylist() == [
        f"https://cdn.test/{PANORAMAX_ID}.jpg",
        f"https://cdn.test/{PANORAMAX_ID}.jpg",
    ]


@pytest.mark.asyncio
async def test_compatible_asset_manifest_skips_without_resolver_calls(tmp_path: Path) -> None:
    manifest, polygon_path, data_root = polygon_fixture(tmp_path)
    resolver = Resolver()
    registry = Registry(resolver)
    with ResolutionCache.open(data_root) as cache:
        first = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=registry,
            stop_requested=lambda: False,
            progress=lambda _event: None,
        )
        second = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=registry,
            stop_requested=lambda: False,
            progress=lambda _event: None,
        )

    assert (first.status, second.status) == ("built", "skipped")
    assert len(resolver.calls) == 1
    assert read_asset_manifest(second.manifest_path, data_root=data_root).counts.rows == 2


@pytest.mark.asyncio
async def test_cache_hit_rebuilds_missing_asset_without_network(tmp_path: Path) -> None:
    manifest, polygon_path, data_root = polygon_fixture(tmp_path)
    resolver = Resolver()
    registry = Registry(resolver)
    with ResolutionCache.open(data_root) as cache:
        first = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=registry,
            stop_requested=lambda: False,
            progress=lambda _event: None,
        )
        first.asset_path.unlink()
        first.manifest_path.unlink()
        resolver.calls.clear()

        rebuilt = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=registry,
            stop_requested=lambda: False,
            progress=lambda _event: None,
        )

    assert rebuilt.status == "built"
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_temporary_failure_manifest_is_rebuilt_for_future_retry(tmp_path: Path) -> None:
    manifest, polygon_path, data_root = polygon_fixture(tmp_path)

    class TemporaryRegistry:
        def __init__(self) -> None:
            self.calls = 0

        async def resolve_reference(
            self,
            reference: SourceReference,
            *,
            bbox: tuple[float, float, float, float],
            resolver_contract_version: int,
        ) -> ResolutionRecord:
            del bbox
            self.calls += 1
            return ResolutionRecord(
                reference.provider,
                reference.canonical_reference,
                resolver_contract_version,
                "temporary_failure",
                (),
                datetime.now(UTC) + timedelta(hours=1),
            )

    registry = TemporaryRegistry()
    with ResolutionCache.open(data_root) as cache:
        first = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=registry,
            stop_requested=lambda: False,
            progress=lambda _event: None,
        )
        second = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=registry,
            stop_requested=lambda: False,
            progress=lambda _event: None,
        )

    assert (first.status, second.status) == ("built", "built")
    assert registry.calls == 1


@pytest.mark.asyncio
async def test_expired_temporary_cache_record_is_resolved_again(tmp_path: Path) -> None:
    manifest, polygon_path, data_root = polygon_fixture(tmp_path)
    resolver = Resolver()
    key = ResolutionKey("panoramax", PANORAMAX_ID, 1)
    with ResolutionCache.open(data_root) as cache:
        cache.put(
            ResolutionRecord(
                key.provider,
                key.canonical_reference,
                key.resolver_contract_version,
                "temporary_failure",
                (),
                datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        result = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=Registry(resolver),
            stop_requested=lambda: False,
            progress=lambda _event: None,
        )

    assert result.status == "built"
    assert len(resolver.calls) == 1


@pytest.mark.asyncio
async def test_expired_direct_url_manifest_and_cache_are_refreshed(tmp_path: Path) -> None:
    manifest, polygon_path, data_root = polygon_fixture(tmp_path)

    class ExpiringRegistry:
        def __init__(self) -> None:
            self.calls = 0

        async def resolve_reference(
            self,
            reference: SourceReference,
            *,
            bbox: tuple[float, float, float, float],
            resolver_contract_version: int,
        ) -> ResolutionRecord:
            del bbox
            self.calls += 1
            expiry = (
                datetime.now(UTC) - timedelta(minutes=1)
                if self.calls == 1
                else datetime.now(UTC) + timedelta(days=1)
            )
            return ResolutionRecord(
                reference.provider,
                reference.canonical_reference,
                resolver_contract_version,
                "resolved",
                (
                    {
                        "image_url": "https://cdn.test/image.jpg",
                        "image_url_expires_at": expiry.isoformat(),
                    },
                ),
                None,
            )

    registry = ExpiringRegistry()
    with ResolutionCache.open(data_root) as cache:
        first = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=registry,
            stop_requested=lambda: False,
            progress=lambda _event: None,
        )
        second = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=registry,
            stop_requested=lambda: False,
            progress=lambda _event: None,
        )

    assert (first.status, second.status) == ("built", "built")
    assert registry.calls == 2


@pytest.mark.asyncio
async def test_stop_before_shard_leaves_no_finalized_partial_output(tmp_path: Path) -> None:
    manifest, polygon_path, data_root = polygon_fixture(tmp_path)
    with ResolutionCache.open(data_root) as cache:
        result = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=Registry(Resolver()),
            stop_requested=lambda: True,
            progress=lambda _event: None,
        )

    assert result.status == "pending"
    assert not result.asset_path.exists()
    assert not result.manifest_path.exists()


def test_resolution_record_accepts_builder_metadata() -> None:
    record = ResolutionRecord(
        "panoramax",
        PANORAMAX_ID,
        1,
        "resolved",
        ({"image_url": "https://cdn.test/image.jpg"},),
        None,
        reason=None,
        category_truncated=False,
    )
    assert record.response_sha256


@pytest.mark.asyncio
async def test_builder_resolves_independent_references_concurrently(tmp_path: Path) -> None:
    manifest, polygon_path, data_root = polygon_fixture(
        tmp_path,
        tags={
            "image": "https://example.test/image.jpg",
            "mapillary": "2627502594079174",
        },
    )

    class ConcurrentRegistry:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

        async def resolve_reference(
            self,
            reference: SourceReference,
            *,
            bbox: tuple[float, float, float, float],
            resolver_contract_version: int,
        ) -> ResolutionRecord:
            del bbox
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.02)
            self.active -= 1
            return ResolutionRecord(
                reference.provider,
                reference.canonical_reference,
                resolver_contract_version,
                "resolved",
                ({"image_url": "https://cdn.test/image.jpg"},),
                None,
            )

    registry = ConcurrentRegistry()
    with ResolutionCache.open(data_root) as cache:
        await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=registry,
            stop_requested=lambda: False,
            progress=lambda _event: None,
        )

    assert registry.max_active == 2
