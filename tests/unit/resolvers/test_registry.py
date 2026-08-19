import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from osm_polygon_image_tag.assets.references import SourceReference
from osm_polygon_image_tag.resolvers.policy import (
    ProviderAccessDenied,
    ProviderNotFound,
    ProviderRateLimited,
    SafeHttpError,
    UnsafeUrlError,
)
from osm_polygon_image_tag.resolvers.registry import (
    ProviderLimit,
    ResolverRegistry,
    _provider_error_result,
)
from osm_polygon_image_tag.resolvers.types import (
    ResolutionResult,
    ResolvedAsset,
    ResolverContext,
)


def test_registry_has_bounded_provider_limits_and_capabilities() -> None:
    registry = ResolverRegistry.build(environment={})

    assert registry.limit_for("wikimedia_commons") == ProviderLimit(4, 2.0)
    assert registry.limit_for("panoramax").max_concurrency <= 8
    assert registry.capability("mapillary") == "anonymous"
    assert registry.capability("flickr") == "anonymous"
    assert registry.capability("wikimedia_commons") == "public"
    assert registry.capability("panoramax") == "public"


def test_registry_reports_direct_capability_with_credentials() -> None:
    registry = ResolverRegistry.build(
        environment={"MAPILLARY_ACCESS_TOKEN": "x", "FLICKR_API_KEY": "y"}
    )

    assert registry.capability("mapillary") == "credentialed"
    assert registry.capability("flickr") == "credentialed"


def test_empty_credentials_are_anonymous() -> None:
    registry = ResolverRegistry.build(
        environment={"MAPILLARY_ACCESS_TOKEN": "", "FLICKR_API_KEY": ""}
    )

    assert registry.capability("mapillary") == "anonymous"
    assert registry.capability("flickr") == "anonymous"


@pytest.mark.parametrize(
    ("error", "status", "reason"),
    [
        (ProviderNotFound("missing"), "not_found", "provider_asset_not_found"),
        (ProviderAccessDenied("denied"), "requires_auth", "provider_access_denied"),
    ],
)
def test_provider_terminal_errors_have_stable_resolution_results(
    error: ProviderNotFound | ProviderAccessDenied, status: str, reason: str
) -> None:
    result = _provider_error_result(error)

    assert result.status == status
    assert result.reason == reason


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


@pytest.mark.asyncio
async def test_rate_limit_delays_following_provider_request() -> None:
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
            if len(observed) == 1:
                raise ProviderRateLimited(120)
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
    for value in ("first", "second"):
        await registry.resolve_reference(
            SourceReference("mapillary", "mapillary", value, value, "mapillary"),
            bbox=(0, 0, 1, 1),
            resolver_contract_version=1,
        )

    assert observed == [0.0, 120.0]


@pytest.mark.asyncio
async def test_registry_turns_transient_http_errors_into_retryable_records() -> None:
    calls = 0
    delays: list[float] = []
    now = datetime(2026, 7, 30, tzinfo=UTC)
    elapsed = 0.0

    class Resolver:
        provider = "mapillary"

        async def resolve(
            self, canonical_reference: str, *, context: ResolverContext
        ) -> ResolutionResult:
            nonlocal calls
            del canonical_reference, context
            calls += 1
            raise SafeHttpError("provider request failed")

    class Http:
        async def aclose(self) -> None:
            return None

    async def no_wait(seconds: float) -> None:
        nonlocal elapsed
        delays.append(seconds)
        elapsed += seconds

    registry = ResolverRegistry(
        {"mapillary": Resolver()},
        environment={},
        http=Http(),
        monotonic=lambda: elapsed,
        sleep=no_wait,
        utcnow=lambda: now,
    )
    reference = SourceReference("mapillary", "mapillary", "id", "id", "mapillary")

    record = await registry.resolve_reference(
        reference,
        bbox=(0, 0, 1, 1),
        resolver_contract_version=1,
    )

    assert record.status == "temporary_failure"
    assert record.reason == "provider_request_failed"
    assert record.retry_after == now + timedelta(minutes=5)
    assert record.attempt_count == 3
    assert calls == 3
    assert delays == [1, 2]


@pytest.mark.asyncio
async def test_registry_records_unsafe_targets_without_retrying() -> None:
    events: list[dict[str, object]] = []
    calls = 0
    delays: list[float] = []

    class Resolver:
        provider = "image"

        async def resolve(
            self, canonical_reference: str, *, context: ResolverContext
        ) -> ResolutionResult:
            nonlocal calls
            del canonical_reference, context
            calls += 1
            raise UnsafeUrlError("host resolved to a non-public IP address")

    class Http:
        async def aclose(self) -> None:
            return None

    async def no_wait(seconds: float) -> None:
        delays.append(seconds)

    registry = ResolverRegistry(
        {"generic_http": Resolver()},
        environment={},
        http=Http(),
        sleep=no_wait,
        progress=events.append,
    )
    reference = SourceReference(
        "image",
        "image",
        "https://example.test/image.jpg",
        "https://example.test/image.jpg",
        "generic_http",
    )

    record = await registry.resolve_reference(
        reference,
        bbox=(0, 0, 1, 1),
        resolver_contract_version=1,
    )

    assert record.status == "invalid_reference"
    assert record.reason == "unsafe_url"
    assert record.attempt_count == 1
    assert calls == 1
    assert delays == []
    assert events == [
        {
            "event": "asset_provider_blocked",
            "provider": "image",
            "reason": "unsafe_url",
        }
    ]


@pytest.mark.asyncio
async def test_registry_records_invalid_reference_without_dispatch() -> None:
    class Http:
        async def aclose(self) -> None:
            return None

    registry = ResolverRegistry({}, environment={}, http=Http())
    reference = SourceReference(
        "mapillary",
        "mapillary",
        "not-an-id",
        "not-an-id",
        "invalid",
    )

    record = await registry.resolve_reference(
        reference,
        bbox=(0, 0, 1, 1),
        resolver_contract_version=1,
    )

    assert record.status == "invalid_reference"
    assert record.reason == "invalid_provider_reference"
