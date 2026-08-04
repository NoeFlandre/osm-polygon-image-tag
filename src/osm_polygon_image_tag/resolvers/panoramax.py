from collections.abc import Mapping

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

_METACATALOG = "https://api.panoramax.xyz/api/pictures"


def _asset_link(assets: Mapping[str, object], *names: str) -> tuple[str | None, str | None]:
    for name in names:
        asset = as_mapping(assets.get(name))
        href = as_text(asset.get("href"))
        if href is not None:
            return href, as_text(asset.get("type"))
    return None, None


def _alternate_link(payload: Mapping[str, object]) -> str | None:
    links = payload.get("links")
    if not isinstance(links, list):
        return None
    for value in links:
        link = as_mapping(value)
        if link.get("rel") in {"alternate", "viewer"}:
            return as_text(link.get("href"))
    return None


class PanoramaxResolver:
    provider = "panoramax"

    def __init__(self, http: MetadataClient) -> None:
        self._http = http

    async def resolve(
        self, canonical_reference: str, *, context: ResolverContext
    ) -> ResolutionResult:
        del context
        payload = await self._http.get_json(f"{_METACATALOG}/{canonical_reference}")
        if not payload:
            return ResolutionResult(status="not_found", reason="picture_missing")
        picture_id = as_text(payload.get("id"))
        if picture_id != canonical_reference:
            return ResolutionResult(status="temporary_failure", reason="picture_id_mismatch")
        assets = as_mapping(payload.get("assets"))
        image_url, mime_type = _asset_link(assets, "hd", "original", "data")
        thumbnail_url, _thumbnail_mime = _asset_link(assets, "thumb", "thumbnail")
        if image_url is None:
            return ResolutionResult(
                status="not_direct_image",
                reason="metacatalog_returned_no_image",
            )
        properties = as_mapping(payload.get("properties"))
        return ResolutionResult(
            status="resolved",
            assets=(
                ResolvedAsset(
                    provider_asset_id=picture_id,
                    page_url=_alternate_link(payload),
                    image_url=image_url,
                    thumbnail_url=thumbnail_url,
                    mime_type=mime_type,
                    width=as_integer(properties.get("width")),
                    height=as_integer(properties.get("height")),
                ),
            ),
        )
