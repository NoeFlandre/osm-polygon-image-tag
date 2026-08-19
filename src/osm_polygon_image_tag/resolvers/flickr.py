from collections.abc import Mapping
from typing import cast
from urllib.parse import urlencode

from osm_polygon_image_tag.resolvers.response import MetadataClient, as_mapping
from osm_polygon_image_tag.resolvers.types import (
    ResolutionResult,
    ResolvedAsset,
    ResolverContext,
)


def _integer_dimension(value: object) -> int | None:
    if not isinstance(value, int):
        return None
    return None if isinstance(value, bool) else value


def _text_dimension(value: object) -> int:
    if not isinstance(value, str):
        return 0
    return int(value) if value.isascii() and value.isdigit() else 0


def _dimension(value: object) -> int:
    integer = _integer_dimension(value)
    return integer if integer is not None else _text_dimension(value)


def _page_asset(canonical_reference: str) -> ResolvedAsset:
    return ResolvedAsset(
        provider_asset_id=canonical_reference,
        page_url=_page_url(canonical_reference),
    )


def _page_url(canonical_reference: str) -> str:
    return f"https://www.flickr.com/photo.gne?id={canonical_reference}"


def _page_only(page_asset: ResolvedAsset, reason: str) -> ResolutionResult:
    return ResolutionResult(status="resolved_page_only", assets=(page_asset,), reason=reason)


def _request_url(canonical_reference: str, key: object) -> str:
    query = urlencode(
        {
            "method": "flickr.photos.getSizes",
            "api_key": key,
            "photo_id": canonical_reference,
            "format": "json",
            "nojsoncallback": "1",
        }
    )
    return f"https://www.flickr.com/services/rest/?{query}"


def _size_candidates(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    sizes = as_mapping(payload.get("sizes")).get("size")
    if not isinstance(sizes, list):
        return []
    candidates = [
        item for item in sizes if isinstance(item, Mapping) and isinstance(item.get("source"), str)
    ]
    return cast(list[Mapping[str, object]], candidates)


def _largest_size(payload: Mapping[str, object]) -> Mapping[str, object] | None:
    candidates = _size_candidates(payload)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: _dimension(item.get("width")) * _dimension(item.get("height")),
    )


async def _resolve_with_key(
    http: MetadataClient,
    canonical_reference: str,
    page_asset: ResolvedAsset,
    key: object,
) -> ResolutionResult:
    payload = await http.get_json(_request_url(canonical_reference, key))
    if payload.get("stat") != "ok":
        return _page_only(page_asset, "sizes_unavailable")
    largest = _largest_size(payload)
    if largest is None:
        return _page_only(page_asset, "sizes_unavailable")
    return ResolutionResult(
        status="resolved",
        assets=(
            ResolvedAsset(
                provider_asset_id=canonical_reference,
                page_url=_page_url(canonical_reference),
                image_url=cast(str, largest.get("source")),
                width=_dimension(largest.get("width")) or None,
                height=_dimension(largest.get("height")) or None,
            ),
        ),
    )


class FlickrResolver:
    provider = "flickr"

    def __init__(self, http: MetadataClient) -> None:
        self._http = http

    async def resolve(
        self, canonical_reference: str, *, context: ResolverContext
    ) -> ResolutionResult:
        page_asset = _page_asset(canonical_reference)
        key = (context.environment or {}).get("FLICKR_API_KEY")
        if not key:
            return _page_only(page_asset, "missing_api_key")
        return await _resolve_with_key(self._http, canonical_reference, page_asset, key)
