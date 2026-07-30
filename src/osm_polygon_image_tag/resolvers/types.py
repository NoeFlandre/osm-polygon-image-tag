from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from osm_polygon_image_tag.assets.schema import validate_status


@dataclass(frozen=True, slots=True)
class ResolvedAsset:
    provider_asset_id: str | None = None
    page_url: str | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None
    image_url_expires_at: datetime | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    license_id: str | None = None
    license_url: str | None = None
    author: str | None = None


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    status: str
    assets: tuple[ResolvedAsset, ...] = ()
    reason: str | None = None
    category_truncated: bool = False
    retry_after: datetime | None = None
    response_sha256: str | None = None

    def __post_init__(self) -> None:
        validate_status(self.status)


@dataclass(frozen=True, slots=True)
class ResolverContext:
    bbox: tuple[float, float, float, float] | None = None
    environment: Mapping[str, str] | None = None


class Resolver(Protocol):
    provider: str

    async def resolve(
        self, canonical_reference: str, *, context: ResolverContext
    ) -> ResolutionResult: ...


@dataclass(frozen=True, slots=True)
class ImageProbe:
    final_url: str
    status_code: int
    mime_type: str | None
    content_length: int | None


type HostResolver = Callable[[str], Awaitable[Sequence[str]]]
