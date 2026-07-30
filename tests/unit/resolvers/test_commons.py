import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from osm_polygon_image_tag.resolvers.commons import CommonsResolver
from osm_polygon_image_tag.resolvers.types import ResolverContext

FIXTURES = Path("tests/fixtures/providers")


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class CommonsHttp:
    def __init__(self, *, category_pages: list[dict[str, object]] | None = None) -> None:
        self.category_pages = list(category_pages or [])
        self.urls: list[str] = []
        self.headers: list[object] = []

    async def get_json(self, url: str, **kwargs: object) -> dict[str, object]:
        self.urls.append(url)
        self.headers.append(kwargs.get("headers"))
        query = parse_qs(urlparse(url).query)
        if query.get("list") == ["categorymembers"]:
            return self.category_pages.pop(0)
        titles = query["titles"][0].split("|")
        if titles == ["File:Jam1.jpg"]:
            return _fixture("commons-file.json")
        pages = {
            str(index): {
                "pageid": index,
                "title": title,
                "imageinfo": [
                    {
                        "url": f"https://upload.wikimedia.org/{index}.jpg",
                        "descriptionurl": f"https://commons.wikimedia.org/wiki/{title}",
                        "mime": "image/jpeg",
                        "width": 100,
                        "height": 50,
                        "extmetadata": {},
                    }
                ],
            }
            for index, title in enumerate(titles, start=1)
        }
        return {"query": {"pages": pages}}


@pytest.mark.asyncio
async def test_commons_file_returns_structured_direct_asset() -> None:
    http = CommonsHttp()
    resolver = CommonsResolver(http)

    result = await resolver.resolve("File:Jam1.jpg", context=ResolverContext())

    assert result.status == "resolved"
    assert len(result.assets) == 1
    asset = result.assets[0]
    assert asset.image_url == "https://upload.wikimedia.org/jam.jpg"
    assert asset.page_url == "https://commons.wikimedia.org/wiki/File:Jam1.jpg"
    assert asset.mime_type == "image/jpeg"
    assert (asset.width, asset.height) == (1200, 800)
    assert asset.license_id == "CC BY-SA 4.0"
    assert asset.author == "Example Author"
    assert http.headers == [
        {
            "User-Agent": (
                "osm-polygon-image-tag/0.1.0 (https://github.com/NoeFlandre/osm-polygon-image-tag)"
            )
        }
    ]


@pytest.mark.asyncio
async def test_commons_category_paginates_and_orders_direct_file_members() -> None:
    http = CommonsHttp(
        category_pages=[
            _fixture("commons-category-page-1.json"),
            _fixture("commons-category-page-2.json"),
        ]
    )
    resolver = CommonsResolver(http)

    result = await resolver.resolve("Brussels Park", context=ResolverContext())

    assert result.status == "resolved"
    assert [asset.page_url for asset in result.assets] == [
        "https://commons.wikimedia.org/wiki/File:Alpha.jpg",
        "https://commons.wikimedia.org/wiki/File:Zeta.jpg",
        "https://commons.wikimedia.org/wiki/File:Omega.jpg",
    ]
    assert all("cmtype=file" in url for url in http.urls[:2])


@pytest.mark.asyncio
async def test_commons_category_cap_is_explicit_and_file_queries_are_batched() -> None:
    members = [{"ns": 6, "pageid": index, "title": f"File:{index:03}.jpg"} for index in range(501)]
    http = CommonsHttp(category_pages=[{"query": {"categorymembers": members}}])
    resolver = CommonsResolver(http, category_cap=500, title_batch_size=50)

    result = await resolver.resolve("Large", context=ResolverContext())

    assert result.status == "category_truncated"
    assert result.category_truncated is True
    assert len(result.assets) == 500
    title_queries = [url for url in http.urls if "titles=" in url]
    assert len(title_queries) == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"query": {"pages": {"-1": {"missing": True, "title": "File:X.jpg"}}}}, "not_found"),
        ({"query": {"pages": {}}}, "not_found"),
        (
            {
                "query": {
                    "pages": {
                        "1": {
                            "pageid": 1,
                            "title": "File:X.webm",
                            "imageinfo": [{"mime": "video/webm", "url": "https://x.test/x"}],
                        }
                    }
                }
            },
            "not_direct_image",
        ),
    ],
)
async def test_commons_file_terminal_statuses(payload: dict[str, object], status: str) -> None:
    class Http:
        async def get_json(self, url: str, **_kwargs: object) -> dict[str, object]:
            del url
            return payload

    result = await CommonsResolver(Http()).resolve("File:X.jpg", context=ResolverContext())

    assert result.status == status


@pytest.mark.asyncio
async def test_commons_malformed_continuation_is_temporary_failure() -> None:
    http = CommonsHttp(
        category_pages=[{"continue": {"continue": "-||"}, "query": {"categorymembers": []}}]
    )

    result = await CommonsResolver(http).resolve("Broken", context=ResolverContext())

    assert result.status == "temporary_failure"
    assert result.reason == "malformed_continuation"
