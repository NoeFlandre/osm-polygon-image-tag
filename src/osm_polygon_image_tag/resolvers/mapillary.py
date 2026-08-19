from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode, urlparse

from osm_polygon_image_tag.resolvers.response import (
    MetadataClient,
    as_integer,
    as_mapping,
    as_text,
)
from osm_polygon_image_tag.resolvers.types import (
    ResolutionResult,
    ResolvedAsset,
    ResolverContext,
)

_FIELDS = "id,thumb_2048_url,thumb_original_url,width,height,captured_at"


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
        token = _access_token(context)
        if not token:
            return _page_only_result(canonical_reference, page_url)
        query = urlencode({"fields": _FIELDS})
        payload = await self._http.get_json(
            f"https://graph.mapillary.com/{canonical_reference}?{query}",
            headers={"Authorization": f"OAuth {token}"},
        )
        error_result = _mapillary_error(payload)
        if error_result is not None:
            return error_result
        asset = _mapillary_asset(payload, canonical_reference, page_url)
        if asset is None:
            return ResolutionResult(status="not_found", reason="mapillary_image_missing")
        return ResolutionResult(
            status="resolved",
            assets=(asset,),
        )


def _access_token(context: ResolverContext) -> str | None:
    return (context.environment or {}).get("MAPILLARY_ACCESS_TOKEN")


def _page_only_result(canonical_reference: str, page_url: str) -> ResolutionResult:
    return ResolutionResult(
        status="resolved_page_only",
        assets=(ResolvedAsset(provider_asset_id=canonical_reference, page_url=page_url),),
        reason="missing_access_token",
    )


def _mapillary_error(payload: Mapping[str, object]) -> ResolutionResult | None:
    error = as_mapping(payload.get("error"))
    if not error:
        return None
    status = "requires_auth" if as_integer(error.get("code")) == 190 else "not_found"
    return ResolutionResult(status=status, reason="mapillary_api_error")


def _mapillary_asset(
    payload: Mapping[str, object], canonical_reference: str, page_url: str
) -> ResolvedAsset | None:
    image_url, thumbnail_url = _mapillary_urls(payload)
    if _no_mapillary_urls(image_url, thumbnail_url):
        return None
    return ResolvedAsset(
        provider_asset_id=_mapillary_asset_id(payload, canonical_reference),
        page_url=page_url,
        image_url=_first_mapillary_url(image_url, thumbnail_url),
        thumbnail_url=thumbnail_url,
        image_url_expires_at=_expiry(image_url or thumbnail_url),
        width=_mapillary_dimension(payload, "width"),
        height=_mapillary_dimension(payload, "height"),
    )


def _mapillary_urls(payload: Mapping[str, object]) -> tuple[str | None, str | None]:
    return as_text(payload.get("thumb_original_url")), as_text(payload.get("thumb_2048_url"))


def _mapillary_dimension(payload: Mapping[str, object], name: str) -> int | None:
    return as_integer(payload.get(name))


def _no_mapillary_urls(image_url: str | None, thumbnail_url: str | None) -> bool:
    return image_url is None and thumbnail_url is None


def _first_mapillary_url(image_url: str | None, thumbnail_url: str | None) -> str | None:
    return image_url or thumbnail_url


def _mapillary_asset_id(payload: Mapping[str, object], canonical_reference: str) -> str:
    return as_text(payload.get("id")) or canonical_reference
