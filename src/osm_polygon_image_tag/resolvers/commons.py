from collections.abc import Mapping, Sequence
from typing import Protocol, cast
from urllib.parse import urlencode

from osm_polygon_image_tag.resolvers.types import (
    ResolutionResult,
    ResolvedAsset,
    ResolverContext,
)

_API = "https://commons.wikimedia.org/w/api.php"
_IMAGE_INFO = "url|mime|size|extmetadata"


class MetadataClient(Protocol):
    async def get_json(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> Mapping[str, object]: ...


def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, list | tuple) else ()


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _metadata_value(metadata: Mapping[str, object], key: str) -> str | None:
    return _text(_mapping(metadata.get(key)).get("value"))


def _asset(page: Mapping[str, object]) -> ResolvedAsset | None:
    info_values = _sequence(page.get("imageinfo"))
    if not info_values:
        return None
    info = _mapping(info_values[0])
    mime_type = _text(info.get("mime"))
    if mime_type is not None and not mime_type.startswith("image/"):
        return None
    metadata = _mapping(info.get("extmetadata"))
    return ResolvedAsset(
        provider_asset_id=str(page["pageid"]) if isinstance(page.get("pageid"), int) else None,
        page_url=_text(info.get("descriptionurl")),
        image_url=_text(info.get("url")),
        mime_type=mime_type,
        width=_integer(info.get("width")),
        height=_integer(info.get("height")),
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
        return await self._http.get_json(f"{_API}?{urlencode({**common, **parameters})}")

    async def _files(self, titles: Sequence[str]) -> ResolutionResult:
        assets_by_title: dict[str, ResolvedAsset] = {}
        saw_non_image = False
        for offset in range(0, len(titles), self._title_batch_size):
            batch = titles[offset : offset + self._title_batch_size]
            payload = await self._request(
                {
                    "prop": "imageinfo",
                    "iiprop": _IMAGE_INFO,
                    "titles": "|".join(batch),
                }
            )
            pages = _sequence(_mapping(payload.get("query")).get("pages"))
            if not pages:
                pages = tuple(_mapping(_mapping(payload.get("query")).get("pages")).values())
            for page_value in pages:
                page = _mapping(page_value)
                if page.get("missing") is True:
                    continue
                title = _text(page.get("title"))
                asset = _asset(page)
                if title is not None and asset is not None:
                    assets_by_title[title] = asset
                elif _sequence(page.get("imageinfo")):
                    saw_non_image = True
        assets = tuple(assets_by_title[title] for title in titles if title in assets_by_title)
        if assets:
            return ResolutionResult(status="resolved", assets=assets)
        if saw_non_image:
            return ResolutionResult(status="not_direct_image", reason="non_image_mime")
        return ResolutionResult(status="not_found", reason="commons_file_missing")

    async def _category(self, title: str) -> ResolutionResult:
        continuation: str | None = None
        members: list[tuple[int, str]] = []
        truncated = False
        while True:
            parameters = {
                "list": "categorymembers",
                "cmtitle": f"Category:{title}",
                "cmnamespace": "6",
                "cmtype": "file",
                "cmlimit": "500",
            }
            if continuation is not None:
                parameters["cmcontinue"] = continuation
            payload = await self._request(parameters)
            for value in _sequence(_mapping(payload.get("query")).get("categorymembers")):
                member = _mapping(value)
                page_id = _integer(member.get("pageid"))
                member_title = _text(member.get("title"))
                if page_id is not None and member_title is not None:
                    members.append((page_id, member_title))
            continue_value = payload.get("continue")
            if continue_value is None:
                break
            continuation = _text(_mapping(continue_value).get("cmcontinue"))
            if continuation is None:
                return ResolutionResult(
                    status="temporary_failure",
                    reason="malformed_continuation",
                )
            if len(members) >= self._category_cap:
                truncated = True
                break
        if len(members) > self._category_cap:
            truncated = True
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

    async def resolve(
        self, canonical_reference: str, *, context: ResolverContext
    ) -> ResolutionResult:
        del context
        if canonical_reference.startswith("File:"):
            return await self._files([canonical_reference])
        return await self._category(canonical_reference)
