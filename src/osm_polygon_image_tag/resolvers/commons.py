from collections.abc import Mapping, Sequence
from urllib.parse import urlencode

from osm_polygon_image_tag.resolvers.response import (
    MetadataClient,
    as_integer,
    as_mapping,
    as_sequence,
    as_text,
)
from osm_polygon_image_tag.resolvers.types import (
    ResolutionResult,
    ResolvedAsset,
    ResolverContext,
)

_API = "https://commons.wikimedia.org/w/api.php"
_IMAGE_INFO = "url|mime|size|extmetadata"
_HEADERS = {
    "User-Agent": (
        "osm-polygon-image-tag/0.1.0 (https://github.com/NoeFlandre/osm-polygon-image-tag)"
    )
}


def _metadata_value(metadata: Mapping[str, object], key: str) -> str | None:
    return as_text(as_mapping(metadata.get(key)).get("value"))


def _asset(page: Mapping[str, object]) -> ResolvedAsset | None:
    info_values = as_sequence(page.get("imageinfo"))
    if not info_values:
        return None
    info = as_mapping(info_values[0])
    mime_type = as_text(info.get("mime"))
    if mime_type is not None and not mime_type.startswith("image/"):
        return None
    metadata = as_mapping(info.get("extmetadata"))
    return ResolvedAsset(
        provider_asset_id=str(page["pageid"]) if isinstance(page.get("pageid"), int) else None,
        page_url=as_text(info.get("descriptionurl")),
        image_url=as_text(info.get("url")),
        mime_type=mime_type,
        width=as_integer(info.get("width")),
        height=as_integer(info.get("height")),
        license_id=_metadata_value(metadata, "LicenseShortName"),
        license_url=_metadata_value(metadata, "LicenseUrl"),
        author=_metadata_value(metadata, "Artist"),
    )


class CommonsResolver:
    provider = "wikimedia_commons"

    def __init__(
        self,
        http: MetadataClient,
        *,
        category_cap: int = 500,
        title_batch_size: int = 50,
    ) -> None:
        self._http = http
        self._category_cap = category_cap
        self._title_batch_size = title_batch_size

    async def _request(self, parameters: Mapping[str, str]) -> Mapping[str, object]:
        common = {"action": "query", "format": "json", "formatversion": "2"}
        return await self._http.get_json(
            f"{_API}?{urlencode({**common, **parameters})}",
            headers=_HEADERS,
        )

    async def _files(self, titles: Sequence[str]) -> ResolutionResult:
        assets_by_title, saw_non_image = await self._collect_files(titles)
        assets = tuple(assets_by_title[title] for title in titles if title in assets_by_title)
        if assets:
            return ResolutionResult(status="resolved", assets=assets)
        if saw_non_image:
            return ResolutionResult(status="not_direct_image", reason="non_image_mime")
        return ResolutionResult(status="not_found", reason="commons_file_missing")

    async def _collect_files(self, titles: Sequence[str]) -> tuple[dict[str, ResolvedAsset], bool]:
        assets: dict[str, ResolvedAsset] = {}
        saw_non_image = False
        for offset in range(0, len(titles), self._title_batch_size):
            batch_assets, batch_non_image = await self._file_batch(
                titles[offset : offset + self._title_batch_size]
            )
            assets.update(batch_assets)
            saw_non_image = saw_non_image or batch_non_image
        return assets, saw_non_image

    async def _file_batch(self, titles: Sequence[str]) -> tuple[dict[str, ResolvedAsset], bool]:
        payload = await self._request(
            {
                "prop": "imageinfo",
                "iiprop": _IMAGE_INFO,
                "titles": "|".join(titles),
            }
        )
        pages = _query_pages(payload)
        assets: dict[str, ResolvedAsset] = {}
        saw_non_image = False
        for page_value in pages:
            title, asset, non_image = _page_asset(page_value)
            if title is not None and asset is not None:
                assets[title] = asset
            saw_non_image = saw_non_image or non_image
        return assets, saw_non_image

    async def _category(self, title: str) -> ResolutionResult:
        members, truncated, malformed = await self._collect_category_members(title)
        if malformed:
            return ResolutionResult(status="temporary_failure", reason="malformed_continuation")
        selected = sorted(members)[: self._category_cap]
        if not selected:
            return ResolutionResult(status="category_empty", reason="no_direct_file_members")
        files = await self._files([member_title for _page_id, member_title in selected])
        return ResolutionResult(
            status="category_truncated" if truncated else files.status,
            assets=files.assets,
            reason=files.reason,
            category_truncated=truncated,
        )

    async def _collect_category_members(
        self, title: str
    ) -> tuple[list[tuple[int, str]], bool, bool]:
        continuation: str | None = None
        members: list[tuple[int, str]] = []
        truncated = False
        while True:
            payload = await self._request(_category_parameters(title, continuation))
            members.extend(_category_members(payload))
            continuation, malformed = _next_category_continuation(payload)
            if malformed:
                return members, truncated, True
            if _category_complete(continuation, len(members), self._category_cap):
                truncated = _category_truncated(continuation, len(members), self._category_cap)
                return members, truncated, False

    async def resolve(
        self, canonical_reference: str, *, context: ResolverContext
    ) -> ResolutionResult:
        del context
        if canonical_reference.startswith("File:"):
            return await self._files([canonical_reference])
        return await self._category(canonical_reference)


def _category_complete(continuation: str | None, member_count: int, cap: int) -> bool:
    return continuation is None or member_count >= cap


def _category_truncated(continuation: str | None, member_count: int, cap: int) -> bool:
    return member_count > cap or continuation is not None


def _query_pages(payload: Mapping[str, object]) -> Sequence[object]:
    pages_value = as_mapping(payload.get("query")).get("pages")
    pages = as_sequence(pages_value)
    return pages or tuple(as_mapping(pages_value).values())


def _page_asset(
    page_value: object,
) -> tuple[str | None, ResolvedAsset | None, bool]:
    page = as_mapping(page_value)
    if page.get("missing") is True:
        return None, None, False
    title = as_text(page.get("title"))
    asset = _asset(page)
    non_image = asset is None and bool(as_sequence(page.get("imageinfo")))
    return title, asset, non_image


def _category_parameters(title: str, continuation: str | None) -> dict[str, str]:
    parameters = {
        "list": "categorymembers",
        "cmtitle": f"Category:{title}",
        "cmnamespace": "6",
        "cmtype": "file",
        "cmlimit": "500",
    }
    if continuation is not None:
        parameters["cmcontinue"] = continuation
    return parameters


def _category_members(payload: Mapping[str, object]) -> list[tuple[int, str]]:
    members: list[tuple[int, str]] = []
    for value in as_sequence(as_mapping(payload.get("query")).get("categorymembers")):
        member = as_mapping(value)
        page_id = as_integer(member.get("pageid"))
        member_title = as_text(member.get("title"))
        if page_id is not None and member_title is not None:
            members.append((page_id, member_title))
    return members


def _next_category_continuation(payload: Mapping[str, object]) -> tuple[str | None, bool]:
    continue_value = payload.get("continue")
    if continue_value is None:
        return None, False
    continuation = as_text(as_mapping(continue_value).get("cmcontinue"))
    return continuation, continuation is None
