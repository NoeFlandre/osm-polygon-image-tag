from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.parse import parse_qs, urlencode, urlparse

from osm_polygon_image_tag.resolvers.types import (
    ResolutionResult,
    ResolvedAsset,
    ResolverContext,
)

_FIELDS = "id,thumb_2048_url,thumb_original_url,width,height,captured_at"


class MetadataClient(Protocol):
    async def get_json(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> Mapping[str, object]: ...


def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _expiry(url: str | None) -> datetime | None:
    if url is None:
        return None
    value = parse_qs(urlparse(url).query).get("Expires", [None])[0]
    if value is not None and value.isascii() and value.isdigit():
        return datetime.fromtimestamp(int(value), tz=UTC)
    return None


class MapillaryResolver:
    provider = "mapillary"

    def __init__(self, http: MetadataClient) -> None:
        self._http = http

    async def resolve(
        self, canonical_reference: str, *, context: ResolverContext
    ) -> ResolutionResult:
        page_url = f"https://www.mapillary.com/app/?pKey={canonical_reference}"
        token = (context.environment or {}).get("MAPILLARY_ACCESS_TOKEN")
        if not token:
            return ResolutionResult(
                status="resolved_page_only",
                assets=(ResolvedAsset(provider_asset_id=canonical_reference, page_url=page_url),),
                reason="missing_access_token",
            )
        query = urlencode({"fields": _FIELDS})
        payload = await self._http.get_json(
            f"https://graph.mapillary.com/{canonical_reference}?{query}",
            headers={"Authorization": f"OAuth {token}"},
        )
        error = _mapping(payload.get("error"))
        if error:
            code = _integer(error.get("code"))
            status = "requires_auth" if code == 190 else "not_found"
            return ResolutionResult(status=status, reason="mapillary_api_error")
        image_url = _text(payload.get("thumb_original_url"))
        thumbnail_url = _text(payload.get("thumb_2048_url"))
        if image_url is None and thumbnail_url is None:
            return ResolutionResult(
                status="not_found",
                reason="mapillary_image_missing",
            )
        return ResolutionResult(
            status="resolved",
            assets=(
                ResolvedAsset(
                    provider_asset_id=_text(payload.get("id")) or canonical_reference,
                    page_url=page_url,
                    image_url=image_url or thumbnail_url,
                    thumbnail_url=thumbnail_url,
                    image_url_expires_at=_expiry(image_url or thumbnail_url),
                    width=_integer(payload.get("width")),
                    height=_integer(payload.get("height")),
                ),
            ),
        )
