import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from osm_polygon_image_tag.assets.references import SourceReference
from osm_polygon_image_tag.assets.resolution import ResolutionRecord
from osm_polygon_image_tag.resolvers.commons import CommonsResolver
from osm_polygon_image_tag.resolvers.flickr import FlickrResolver
from osm_polygon_image_tag.resolvers.generic import GenericImageResolver
from osm_polygon_image_tag.resolvers.http import SafeHttpClient
from osm_polygon_image_tag.resolvers.kartaview import KartaViewResolver
from osm_polygon_image_tag.resolvers.mapillary import MapillaryResolver
from osm_polygon_image_tag.resolvers.panoramax import PanoramaxResolver
from osm_polygon_image_tag.resolvers.policy import (
    ProviderAccessDenied,
    ProviderNotFound,
    ProviderRateLimited,
    SafeHttpError,
    UnsafeUrlError,
)
from osm_polygon_image_tag.resolvers.streetside import StreetsideResolver
from osm_polygon_image_tag.resolvers.types import ResolutionResult, Resolver, ResolverContext


@dataclass(frozen=True, slots=True)
class ProviderLimit:
    max_concurrency: int
    requests_per_second: float


_LIMITS = {
    "wikimedia_commons": ProviderLimit(4, 2.0),
    "panoramax": ProviderLimit(8, 4.0),
    "mapillary": ProviderLimit(4, 2.0),
    "kartaview": ProviderLimit(2, 1.0),
    "flickr": ProviderLimit(2, 1.0),
    "streetside": ProviderLimit(8, 10.0),
    "image": ProviderLimit(4, 2.0),
}
_TRANSIENT_ATTEMPTS = 3
_TRANSIENT_RETRY_SECONDS = 300


class ClosingClient(Protocol):
    async def aclose(self) -> None: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ResolverRegistry:
    def __init__(
        self,
        resolvers: Mapping[str, Resolver],
        *,
        environment: Mapping[str, str],
        http: ClosingClient,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        progress: Callable[[dict[str, object]], None] | None = None,
        utcnow: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._resolvers = dict(resolvers)
        self.environment = dict(environment)
        self._http = http
        self._semaphores = {
            provider: asyncio.Semaphore(limit.max_concurrency)
            for provider, limit in _LIMITS.items()
        }
        self._rate_locks = {provider: asyncio.Lock() for provider in _LIMITS}
        self._next_request = {provider: 0.0 for provider in _LIMITS}
        self._monotonic = monotonic
        self._sleep = sleep
        self._progress = progress or (lambda _event: None)
        self._utcnow = utcnow

    @classmethod
    def build(
        cls,
        *,
        environment: Mapping[str, str],
        http: SafeHttpClient | None = None,
        progress: Callable[[dict[str, object]], None] | None = None,
    ) -> "ResolverRegistry":
        client = http or SafeHttpClient()
        commons = CommonsResolver(client)
        resolvers: dict[str, Resolver] = {
            "commons_category": commons,
            "commons_file": commons,
            "panoramax": PanoramaxResolver(client),
            "mapillary": MapillaryResolver(client),
            "kartaview": KartaViewResolver(client),
            "flickr": FlickrResolver(client),
            "streetside": StreetsideResolver(),
            "generic_http": GenericImageResolver(client),
        }
        return cls(
            resolvers,
            environment=environment,
            http=client,
            progress=progress,
        )

    def resolver_for(self, kind: str) -> Resolver:
        return self._resolvers[kind]

    async def resolve_reference(
        self,
        reference: SourceReference,
        *,
        bbox: tuple[float, float, float, float],
        resolver_contract_version: int,
    ) -> ResolutionRecord:
        if reference.resolver_kind == "invalid":
            return ResolutionRecord(
                provider=reference.provider,
                canonical_reference=reference.canonical_reference,
                resolver_contract_version=resolver_contract_version,
                status="invalid_reference",
                assets=(),
                retry_after=None,
                reason="invalid_provider_reference",
                attempt_count=0,
            )
        async with self._semaphores[reference.provider]:
            attempt_count = 0
            while True:
                attempt_count += 1
                await self._pace(reference.provider)
                try:
                    result = await self.resolver_for(reference.resolver_kind).resolve(
                        reference.canonical_reference,
                        context=ResolverContext(bbox=bbox, environment=self.environment),
                    )
                except ProviderNotFound:
                    result = ResolutionResult(
                        status="not_found",
                        reason="provider_asset_not_found",
                    )
                except ProviderAccessDenied:
                    result = ResolutionResult(
                        status="requires_auth",
                        reason="provider_access_denied",
                    )
                except ProviderRateLimited as error:
                    seconds = error.retry_after_seconds
                    if seconds is not None:
                        await self._cooldown(reference.provider, seconds)
                    self._progress(
                        {
                            "event": "asset_provider_cooldown",
                            "provider": reference.provider,
                            "retry_after_seconds": seconds,
                        }
                    )
                    result = ResolutionResult(
                        status="temporary_failure",
                        retry_after=(
                            self._utcnow() + timedelta(seconds=seconds)
                            if seconds is not None
                            else self._utcnow() + timedelta(seconds=_TRANSIENT_RETRY_SECONDS)
                        ),
                        reason="provider_rate_limited",
                    )
                except UnsafeUrlError:
                    raise
                except SafeHttpError:
                    if attempt_count < _TRANSIENT_ATTEMPTS:
                        delay = 2 ** (attempt_count - 1)
                        self._progress(
                            {
                                "event": "asset_provider_retry",
                                "provider": reference.provider,
                                "attempt": attempt_count + 1,
                                "delay_seconds": delay,
                            }
                        )
                        await self._sleep(delay)
                        continue
                    result = ResolutionResult(
                        status="temporary_failure",
                        retry_after=self._utcnow() + timedelta(seconds=_TRANSIENT_RETRY_SECONDS),
                        reason="provider_request_failed",
                    )
                break
        assets: list[dict[str, object]] = []
        for asset in result.assets:
            payload = asdict(asset)
            for key, value in payload.items():
                if isinstance(value, datetime):
                    payload[key] = value.isoformat()
            assets.append(payload)
        return ResolutionRecord(
            provider=reference.provider,
            canonical_reference=reference.canonical_reference,
            resolver_contract_version=resolver_contract_version,
            status=result.status,
            assets=tuple(assets),
            retry_after=result.retry_after,
            reason=result.reason,
            category_truncated=result.category_truncated,
            attempt_count=attempt_count,
        )

    async def _pace(self, provider: str) -> None:
        async with self._rate_locks[provider]:
            delay = self._next_request[provider] - self._monotonic()
            if delay > 0:
                await self._sleep(delay)
            interval = 1.0 / _LIMITS[provider].requests_per_second
            self._next_request[provider] = self._monotonic() + interval

    async def _cooldown(self, provider: str, seconds: int) -> None:
        async with self._rate_locks[provider]:
            deadline = self._monotonic() + seconds
            self._next_request[provider] = max(
                self._next_request[provider],
                deadline,
            )

    def limit_for(self, provider: str) -> ProviderLimit:
        return _LIMITS[provider]

    def capability(self, provider: str) -> str:
        if provider == "mapillary" and "MAPILLARY_ACCESS_TOKEN" not in self.environment:
            return "page_only_missing_token"
        if provider == "flickr" and "FLICKR_API_KEY" not in self.environment:
            return "page_only_missing_key"
        if provider == "streetside":
            return "page_only"
        return "direct"

    async def aclose(self) -> None:
        await self._http.aclose()
