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
        error = as_mapping(payload.get("error"))
        if error:
            code = as_integer(error.get("code"))
            status = "requires_auth" if code == 190 else "not_found"
            return ResolutionResult(status=status, reason="mapillary_api_error")
        image_url = as_text(payload.get("thumb_original_url"))
        thumbnail_url = as_text(payload.get("thumb_2048_url"))
        if image_url is None and thumbnail_url is None:
            return ResolutionResult(
                status="not_found",
                reason="mapillary_image_missing",
            )
        return ResolutionResult(
            status="resolved",
            assets=(
                ResolvedAsset(
                    provider_asset_id=as_text(payload.get("id")) or canonical_reference,
                    page_url=page_url,
                    image_url=image_url or thumbnail_url,
                    thumbnail_url=thumbnail_url,
                    image_url_expires_at=_expiry(image_url or thumbnail_url),
                    width=as_integer(payload.get("width")),
                    height=as_integer(payload.get("height")),
                ),
            ),
        )
