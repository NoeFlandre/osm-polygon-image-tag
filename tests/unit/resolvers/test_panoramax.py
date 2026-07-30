import json
from pathlib import Path

import pytest

from osm_polygon_image_tag.resolvers.panoramax import PanoramaxResolver
from osm_polygon_image_tag.resolvers.types import ResolverContext

PICTURE_ID = "4492cea4-1018-4285-8074-cf3d37f3c673"


class Http:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.urls: list[str] = []

    async def get_json(self, url: str, **_kwargs: object) -> dict[str, object]:
        self.urls.append(url)
        return self.payload


@pytest.mark.asyncio
async def test_panoramax_uses_metacatalog_and_returned_links_only() -> None:
    payload = json.loads(
        Path("tests/fixtures/providers/panoramax-picture.json").read_text(encoding="utf-8")
    )
    http = Http(payload)

    result = await PanoramaxResolver(http).resolve(PICTURE_ID, context=ResolverContext())

    assert http.urls == [f"https://api.panoramax.xyz/api/pictures/{PICTURE_ID}"]
    assert result.status == "resolved"
    asset = result.assets[0]
    assert asset.provider_asset_id == PICTURE_ID
    assert asset.image_url == "https://cdn.panoramax.test/full.jpg"
    assert asset.thumbnail_url == "https://cdn.panoramax.test/thumb.jpg"
    assert asset.page_url == (
        "https://viewer.panoramax.test/#focus=pic&pic=4492cea4-1018-4285-8074-cf3d37f3c673"
    )
    assert (asset.width, asset.height) == (4096, 2048)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({}, "not_found"),
        ({"id": PICTURE_ID, "assets": {}, "links": []}, "not_direct_image"),
        ({"id": "different", "assets": {}, "links": []}, "temporary_failure"),
    ],
)
async def test_panoramax_explicit_terminal_statuses(
    payload: dict[str, object], status: str
) -> None:
    result = await PanoramaxResolver(Http(payload)).resolve(PICTURE_ID, context=ResolverContext())

    assert result.status == status
