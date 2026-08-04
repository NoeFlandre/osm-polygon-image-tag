from collections.abc import Mapping
from typing import cast
from urllib.parse import urlencode

from osm_polygon_image_tag.resolvers.response import MetadataClient, as_mapping
from osm_polygon_image_tag.resolvers.types import (
    ResolutionResult,
    ResolvedAsset,
    ResolverContext,
)


def _dimension(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return 0


class FlickrResolver:
    provider = "flickr"

    def __init__(self, http: MetadataClient) -> None:
        self._http = http

    async def resolve(
        self, canonical_reference: str, *, context: ResolverContext
    ) -> ResolutionResult:
        page_url = f"https://www.flickr.com/photo.gne?id={canonical_reference}"
        key = (context.environment or {}).get("FLICKR_API_KEY")
        page_asset = ResolvedAsset(
            provider_asset_id=canonical_reference,
            page_url=page_url,
        )
        if not key:
            return ResolutionResult(
                status="resolved_page_only",
                assets=(page_asset,),
                reason="missing_api_key",
            )
        query = urlencode(
            {
                "method": "flickr.photos.getSizes",
                "api_key": key,
                "photo_id": canonical_reference,
                "format": "json",
                "nojsoncallback": "1",
            }
        )
        payload = await self._http.get_json(f"https://www.flickr.com/services/rest/?{query}")
        if payload.get("stat") != "ok":
            return ResolutionResult(
                status="resolved_page_only",
                assets=(page_asset,),
                reason="sizes_unavailable",
            )
        sizes = as_mapping(payload.get("sizes")).get("size")
        if not isinstance(sizes, list):
            return ResolutionResult(
                status="resolved_page_only",
                assets=(page_asset,),
                reason="sizes_unavailable",
            )
        candidates = [
            item
            for item in sizes
            if isinstance(item, Mapping) and isinstance(item.get("source"), str)
        ]
        if not candidates:
            return ResolutionResult(
                status="resolved_page_only",
                assets=(page_asset,),
                reason="sizes_unavailable",
            )
        typed_candidates = cast(list[Mapping[str, object]], candidates)
        largest = max(
            typed_candidates,
            key=lambda item: _dimension(item.get("width")) * _dimension(item.get("height")),
        )
        return ResolutionResult(
            status="resolved",
            assets=(
                ResolvedAsset(
                    provider_asset_id=canonical_reference,
                    page_url=page_url,
                    image_url=cast(str, largest.get("source")),
                    width=_dimension(largest.get("width")) or None,
                    height=_dimension(largest.get("height")) or None,
                ),
            ),
        )
