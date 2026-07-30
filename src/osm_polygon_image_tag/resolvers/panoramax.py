from collections.abc import Mapping
from typing import Protocol, cast

from osm_polygon_image_tag.resolvers.types import (
    ResolutionResult,
    ResolvedAsset,
    ResolverContext,
)

_METACATALOG = "https://api.panoramax.xyz/api/pictures"


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


def _asset_link(assets: Mapping[str, object], *names: str) -> tuple[str | None, str | None]:
    for name in names:
        asset = _mapping(assets.get(name))
        href = _text(asset.get("href"))
        if href is not None:
            return href, _text(asset.get("type"))
    return None, None


def _alternate_link(payload: Mapping[str, object]) -> str | None:
    links = payload.get("links")
    if not isinstance(links, list):
        return None
    for value in links:
        link = _mapping(value)
        if link.get("rel") in {"alternate", "viewer"}:
            return _text(link.get("href"))
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
        picture_id = _text(payload.get("id"))
        if picture_id != canonical_reference:
            return ResolutionResult(status="temporary_failure", reason="picture_id_mismatch")
        assets = _mapping(payload.get("assets"))
        image_url, mime_type = _asset_link(assets, "hd", "original", "data")
        thumbnail_url, _thumbnail_mime = _asset_link(assets, "thumb", "thumbnail")
        if image_url is None:
            return ResolutionResult(
                status="not_direct_image",
                reason="metacatalog_returned_no_image",
            )
        properties = _mapping(payload.get("properties"))
        return ResolutionResult(
            status="resolved",
            assets=(
                ResolvedAsset(
                    provider_asset_id=picture_id,
                    page_url=_alternate_link(payload),
                    image_url=image_url,
                    thumbnail_url=thumbnail_url,
                    mime_type=mime_type,
                    width=_integer(properties.get("width")),
                    height=_integer(properties.get("height")),
                ),
            ),
        )
