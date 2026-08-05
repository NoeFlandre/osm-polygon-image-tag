import asyncio
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from json import dumps as json_dumps
from pathlib import Path
from typing import cast

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
from osm_polygon_image_tag.assets.polygon_input import POLYGON_COLUMNS, REFERENCE_COLUMNS
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

    def capability(self, provider: str) -> str:
        del provider
        return "public"

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
async def test_builder_prunes_progress_count_to_reference_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, polygon_path, data_root = polygon_fixture(tmp_path)
    original_iter_batches = pq.ParquetFile.iter_batches
    seen_columns: list[tuple[str, ...]] = []

    def capture_iter_batches(parquet: pq.ParquetFile, *args: object, **kwargs: object) -> object:
        columns = kwargs.get("columns")
        if isinstance(columns, list):
            seen_columns.append(tuple(cast(list[str], columns)))
        return original_iter_batches(parquet, *args, **kwargs)

    monkeypatch.setattr(pq.ParquetFile, "iter_batches", capture_iter_batches)
    with ResolutionCache.open(data_root) as cache:
        result = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=Registry(Resolver()),
            stop_requested=lambda: False,
            progress=lambda _event: None,
        )

    assert result.status == "built"
    assert seen_columns[:2] == [REFERENCE_COLUMNS, POLYGON_COLUMNS]


@pytest.mark.asyncio
async def test_builder_reuses_resolved_records_for_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, polygon_path, data_root = polygon_fixture(
        tmp_path,
        tags={
            "panoramax": PANORAMAX_ID,
            "panoramax:0": "second-picture",
        },
    )
    cached = ResolutionRecord(
        "panoramax",
        PANORAMAX_ID,
        1,
        "resolved",
        ({"image_url": f"https://cdn.test/{PANORAMAX_ID}.jpg"},),
        None,
    )
    resolver = Resolver()

    with ResolutionCache.open(data_root) as cache:
        cache.put(cached)
        original_get = cache.get
        get_calls: list[ResolutionKey] = []

        def count_get(key: ResolutionKey) -> ResolutionRecord | None:
            get_calls.append(key)
            return original_get(key)

        monkeypatch.setattr(cache, "get", count_get)

        result = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=Registry(resolver),
            stop_requested=lambda: False,
            progress=lambda _event: None,
            resolver_contract_version=1,
        )

    counts = read_asset_manifest(result.manifest_path, data_root=data_root).counts
    assert result.status == "built"
    assert get_calls == [cached.key, ResolutionKey("panoramax", "second-picture", 1)]
    assert counts.cache_hits == 1
    assert counts.resolver_requests == 1


@pytest.mark.asyncio
async def test_builder_batches_fresh_cache_records_per_flush(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, polygon_path, data_root = polygon_fixture(
        tmp_path,
        tags={"panoramax": PANORAMAX_ID, "panoramax:0": "second-picture"},
    )
    resolver = Resolver()

    with ResolutionCache.open(data_root) as cache:
        calls: list[tuple[ResolutionRecord, ...]] = []
        original_put_many = cache.put_many

        def capture_put_many(records: list[ResolutionRecord]) -> None:
            calls.append(tuple(records))
            original_put_many(records)

        monkeypatch.setattr(cache, "put_many", capture_put_many)
        result = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=Registry(resolver),
            stop_requested=lambda: False,
            progress=lambda _event: None,
            resolver_contract_version=1,
        )

    assert result.status == "built"
    assert [[record.canonical_reference for record in batch] for batch in calls] == [
        [PANORAMAX_ID, "second-picture"]
    ]


@pytest.mark.asyncio
async def test_builder_globally_sorts_rows_across_bounded_chunks(tmp_path: Path) -> None:
    manifest, polygon_path, data_root = polygon_fixture(tmp_path)
    polygon_rows = []
    for osm_id in range(130, 0, -1):
        row = _polygon_row({"panoramax": PANORAMAX_ID})
        row["osm_id"] = osm_id
        polygon_rows.append(row)
    write_geoparquet(polygon_rows, polygon_path)
    manifest = Manifest(
        manifest.manifest_schema_version,
        manifest.processing_contract_version,
        manifest.dataset_schema_version,
        manifest.source,
        OutputIdentity(
            manifest.output.relative_path,
            polygon_path.stat().st_size,
            file_sha256(polygon_path),
            len(polygon_rows),
        ),
        manifest.osmium_version,
        RunCounts(len(polygon_rows), {}),
    )
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

    assert pq.read_table(result.asset_path, columns=["osm_id"]).column(0).to_pylist() == list(
        range(1, 131)
    )
    assert len(resolver.calls) == 1
    assert not list(result.asset_path.parent.glob(".asset-sort.*"))


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
    counts = read_asset_manifest(rebuilt.manifest_path, data_root=data_root).counts
    assert counts.cache_hits == 1
    assert counts.resolver_requests == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "tag_value"),
    [
        ("mapillary", "2627502594079174"),
        ("flickr", "6831725321"),
    ],
)
@pytest.mark.parametrize("initial_status", ["resolved_page_only", "requires_auth"])
async def test_credentialed_resume_refreshes_auth_limited_provider_results(
    tmp_path: Path,
    provider: str,
    tag_value: str,
    initial_status: str,
) -> None:
    manifest, polygon_path, data_root = polygon_fixture(
        tmp_path,
        tags={provider: tag_value},
    )

    class CredentialRegistry:
        def __init__(self) -> None:
            self.credentialed = False
            self.calls = 0

        def capability(self, provider: str) -> str:
            assert provider in {"mapillary", "flickr"}
            return "credentialed" if self.credentialed else "anonymous"

        async def resolve_reference(
            self,
            reference: SourceReference,
            *,
            bbox: tuple[float, float, float, float],
            resolver_contract_version: int,
        ) -> ResolutionRecord:
            del bbox
            self.calls += 1
            if not self.credentialed:
                return ResolutionRecord(
                    reference.provider,
                    reference.canonical_reference,
                    resolver_contract_version,
                    initial_status,
                    (),
                    None,
                )
            return ResolutionRecord(
                reference.provider,
                reference.canonical_reference,
                resolver_contract_version,
                "resolved",
                ({"image_url": "https://scontent.test/direct.jpg"},),
                None,
            )

    registry = CredentialRegistry()
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
        registry.credentialed = True
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
    assert pq.read_table(second.asset_path).column("image_url").to_pylist() == [
        "https://scontent.test/direct.jpg"
    ]


@pytest.mark.asyncio
async def test_public_commons_resume_refreshes_old_requires_auth_result(tmp_path: Path) -> None:
    manifest, polygon_path, data_root = polygon_fixture(
        tmp_path,
        tags={"wikimedia_commons": "File:Example.jpg"},
    )

    class CommonsRegistry:
        def __init__(self) -> None:
            self.calls = 0

        def capability(self, provider: str) -> str:
            assert provider == "wikimedia_commons"
            return "public"

        async def resolve_reference(
            self,
            reference: SourceReference,
            *,
            bbox: tuple[float, float, float, float],
            resolver_contract_version: int,
        ) -> ResolutionRecord:
            del bbox
            self.calls += 1
            status = "requires_auth" if self.calls == 1 else "resolved"
            assets: tuple[dict[str, object], ...] = (
                ()
                if self.calls == 1
                else ({"image_url": "https://upload.wikimedia.org/example.jpg"},)
            )
            return ResolutionRecord(
                reference.provider,
                reference.canonical_reference,
                resolver_contract_version,
                status,
                assets,
                None,
            )

    registry = CommonsRegistry()
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
async def test_credential_does_not_rebuild_unrelated_page_only_rows(tmp_path: Path) -> None:
    manifest, polygon_path, data_root = polygon_fixture(
        tmp_path,
        tags={
            "mapillary": "2627502594079174",
            "image": "https://example.test/page",
        },
    )

    class MixedRegistry:
        def __init__(self) -> None:
            self.calls = 0

        def capability(self, provider: str) -> str:
            return "credentialed" if provider == "mapillary" else "public"

        async def resolve_reference(
            self,
            reference: SourceReference,
            *,
            bbox: tuple[float, float, float, float],
            resolver_contract_version: int,
        ) -> ResolutionRecord:
            del bbox
            self.calls += 1
            status = "resolved" if reference.provider == "mapillary" else "resolved_page_only"
            return ResolutionRecord(
                reference.provider,
                reference.canonical_reference,
                resolver_contract_version,
                status,
                ({"page_url": reference.canonical_reference},),
                None,
            )

    registry = MixedRegistry()
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
    assert registry.calls == 2


@pytest.mark.asyncio
async def test_temporary_failure_manifest_is_reused_until_retry_is_due(tmp_path: Path) -> None:
    manifest, polygon_path, data_root = polygon_fixture(tmp_path)

    class TemporaryRegistry:
        def __init__(self) -> None:
            self.calls = 0

        def capability(self, provider: str) -> str:
            del provider
            return "public"

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

    assert (first.status, second.status) == ("built", "skipped")
    assert registry.calls == 1


@pytest.mark.asyncio
async def test_due_temporary_failure_manifest_is_rebuilt(tmp_path: Path) -> None:
    manifest, polygon_path, data_root = polygon_fixture(tmp_path)

    class DueTemporaryRegistry:
        def __init__(self) -> None:
            self.calls = 0

        def capability(self, provider: str) -> str:
            del provider
            return "public"

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
                datetime.now(UTC) - timedelta(seconds=1),
            )

    registry = DueTemporaryRegistry()
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

        def capability(self, provider: str) -> str:
            del provider
            return "public"

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

        def capability(self, provider: str) -> str:
            del provider
            return "public"

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


# A deliberately fake, clearly non-real query value. Never copy real source keys.
NON_CACHEABLE_IMAGE_URL = "https://photos.test/share/abc/photo/xyz?key=redacted-test-secret-token"


@pytest.mark.asyncio
async def test_non_cacheable_reference_is_resolved_without_caching_or_snapshot(
    tmp_path: Path,
) -> None:
    manifest, polygon_path, data_root = polygon_fixture(
        tmp_path, tags={"image": NON_CACHEABLE_IMAGE_URL}
    )
    resolver = Resolver()
    events: list[dict[str, object]] = []
    with ResolutionCache.open(data_root) as cache:
        result = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=Registry(resolver),
            stop_requested=lambda: False,
            progress=events.append,
        )
        cached_rows = cache._connection.execute("SELECT COUNT(*) FROM resolutions").fetchone()[0]
        manifest_obj = read_asset_manifest(result.manifest_path, data_root=data_root)

    assert result.status == "built"
    assert len(resolver.calls) == 1
    # The secret reference is resolved but never persisted to the cache.
    assert cached_rows == 0
    assert manifest_obj.resolution_snapshot.entry_count == 0
    manifest_text = result.manifest_path.read_text(encoding="utf-8")
    assert "redacted-test-secret-token" not in manifest_text
    assert all("redacted-test-secret-token" not in json_dumps(event) for event in events)
    # Direct image resolution is preserved where safe.
    table = pq.read_table(result.asset_path)
    assert table.column("status").to_pylist() == ["resolved"]
    assert table.column("image_url").to_pylist() == [
        f"https://cdn.test/{NON_CACHEABLE_IMAGE_URL}.jpg"
    ]


@pytest.mark.asyncio
async def test_cacheable_reference_caches_but_non_cacheable_is_always_resolved(
    tmp_path: Path,
) -> None:
    tags = {
        "panoramax": PANORAMAX_ID,
        "image": NON_CACHEABLE_IMAGE_URL,
    }
    manifest, polygon_path, data_root = polygon_fixture(tmp_path, tags=tags)

    class TrackingRegistry:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def capability(self, provider: str) -> str:
            del provider
            return "public"

        async def resolve_reference(
            self,
            reference: SourceReference,
            *,
            bbox: tuple[float, float, float, float],
            resolver_contract_version: int,
        ) -> ResolutionRecord:
            del bbox
            self.calls.append(reference.canonical_reference)
            return ResolutionRecord(
                reference.provider,
                reference.canonical_reference,
                resolver_contract_version,
                "resolved",
                ({"image_url": "https://cdn.test/direct.jpg"},),
                None,
            )

    registry = TrackingRegistry()
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
        registry.calls.clear()

        second = await build_asset_shard(
            manifest,
            polygon_path,
            data_root,
            cache=cache,
            registry=registry,
            stop_requested=lambda: False,
            progress=lambda _event: None,
        )
        second_counts = read_asset_manifest(second.manifest_path, data_root=data_root).counts

    assert (first.status, second.status) == ("built", "built")
    # The cacheable reference is a cache hit on rebuild; only the non-cacheable
    # reference is resolved again.
    assert registry.calls == [NON_CACHEABLE_IMAGE_URL]
    assert second_counts.cache_hits == 1
    assert second_counts.resolver_requests == 1
