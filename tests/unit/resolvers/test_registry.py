import asyncio
from datetime import UTC, datetime

import pytest

from osm_polygon_image_tag.assets.references import SourceReference
from osm_polygon_image_tag.resolvers.policy import ProviderRateLimited
from osm_polygon_image_tag.resolvers.registry import ProviderLimit, ResolverRegistry
from osm_polygon_image_tag.resolvers.types import (
    ResolutionResult,
    ResolvedAsset,
    ResolverContext,
)


def test_registry_has_bounded_provider_limits_and_capabilities() -> None:
    registry = ResolverRegistry.build(environment={})

    assert registry.limit_for("wikimedia_commons") == ProviderLimit(4, 2.0)
    assert registry.limit_for("panoramax").max_concurrency <= 8
    assert registry.capability("mapillary") == "page_only_missing_token"
    assert registry.capability("flickr") == "page_only_missing_key"


def test_registry_reports_direct_capability_with_credentials() -> None:
    registry = ResolverRegistry.build(
        environment={"MAPILLARY_ACCESS_TOKEN": "x", "FLICKR_API_KEY": "y"}
    )

    assert registry.capability("mapillary") == "direct"
    assert registry.capability("flickr") == "direct"


@pytest.mark.asyncio
async def test_registry_enforces_provider_concurrency_limit() -> None:
    class Resolver:
        provider = "mapillary"

        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

        async def resolve(
            self, canonical_reference: str, *, context: ResolverContext
        ) -> ResolutionResult:
            del canonical_reference, context
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.02)
            self.active -= 1
            return ResolutionResult(
                status="resolved",
                assets=(ResolvedAsset(image_url="https://cdn.test/image.jpg"),),
            )

    class Http:
        async def aclose(self) -> None:
            return None

    async def no_wait(_seconds: float) -> None:
        return None

    resolver = Resolver()
    registry = ResolverRegistry(
        {"mapillary": resolver},
        environment={},
        http=Http(),
        monotonic=lambda: 0.0,
        sleep=no_wait,
    )
    references = [
        SourceReference("mapillary", "mapillary", str(index), str(index), "mapillary")
        for index in range(8)
    ]

    await asyncio.gather(
        *(
            registry.resolve_reference(
                reference,
                bbox=(0, 0, 1, 1),
                resolver_contract_version=1,
            )
            for reference in references
        )
    )

    assert resolver.max_active == 4


@pytest.mark.asyncio
async def test_registry_paces_requests_per_provider() -> None:
    now = 0.0
    observed: list[float] = []

    async def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    class Resolver:
        provider = "mapillary"

        async def resolve(
            self, canonical_reference: str, *, context: ResolverContext
        ) -> ResolutionResult:
            del canonical_reference, context
            observed.append(now)
            return ResolutionResult(status="not_found")

    class Http:
        async def aclose(self) -> None:
            return None

    registry = ResolverRegistry(
        {"mapillary": Resolver()},
        environment={},
        http=Http(),
        monotonic=lambda: now,
        sleep=sleep,
    )
    references = [
        SourceReference("mapillary", "mapillary", str(index), str(index), "mapillary")
        for index in range(3)
    ]

    await asyncio.gather(
        *(
            registry.resolve_reference(
                reference,
                bbox=(0, 0, 1, 1),
                resolver_contract_version=1,
            )
            for reference in references
        )
    )

    assert observed == [0.0, 0.5, 1.0]


@pytest.mark.asyncio
async def test_registry_turns_rate_limits_into_retryable_records_and_progress() -> None:
    events: list[dict[str, object]] = []
    now = datetime(2026, 7, 30, tzinfo=UTC)

    class Resolver:
        provider = "mapillary"

        async def resolve(
            self, canonical_reference: str, *, context: ResolverContext
        ) -> ResolutionResult:
            del canonical_reference, context
            raise ProviderRateLimited(120)

    class Http:
        async def aclose(self) -> None:
            return None

    registry = ResolverRegistry(
        {"mapillary": Resolver()},
        environment={},
        http=Http(),
        progress=events.append,
        utcnow=lambda: now,
    )
    reference = SourceReference("mapillary", "mapillary", "id", "id", "mapillary")

    record = await registry.resolve_reference(
        reference,
        bbox=(0, 0, 1, 1),
        resolver_contract_version=1,
    )

    assert record.status == "temporary_failure"
    assert record.retry_after == datetime(2026, 7, 30, 0, 2, tzinfo=UTC)
    assert events == [
        {
            "event": "asset_provider_cooldown",
            "provider": "mapillary",
            "retry_after_seconds": 120,
        }
    ]
