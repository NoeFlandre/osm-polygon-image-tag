import pytest

from osm_polygon_image_tag.resolvers.flickr import FlickrResolver
from osm_polygon_image_tag.resolvers.types import ResolverContext


class Http:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.urls: list[str] = []

    async def get_json(self, url: str, **_kwargs: object) -> dict[str, object]:
        self.urls.append(url)
        return self.payload


@pytest.mark.asyncio
async def test_flickr_without_key_is_stable_page_only() -> None:
    result = await FlickrResolver(Http({})).resolve(
        "6831725321", context=ResolverContext(environment={})
    )

    assert result.status == "resolved_page_only"
    assert result.assets[0].page_url == "https://www.flickr.com/photo.gne?id=6831725321"


@pytest.mark.asyncio
async def test_flickr_selects_largest_returned_public_size() -> None:
    http = Http(
        {
            "stat": "ok",
            "sizes": {
                "size": [
                    {"source": "https://live.test/small.jpg", "width": 100, "height": 50},
                    {
                        "source": "https://live.test/original.jpg",
                        "width": 4000,
                        "height": 2000,
                    },
                ]
            },
        }
    )

    result = await FlickrResolver(http).resolve(
        "6831725321", context=ResolverContext(environment={"FLICKR_API_KEY": "secret"})
    )

    assert result.status == "resolved"
    assert result.assets[0].image_url == "https://live.test/original.jpg"
    assert "api_key=secret" in http.urls[0]


@pytest.mark.asyncio
async def test_flickr_permission_limited_response_is_page_only() -> None:
    result = await FlickrResolver(Http({"stat": "fail", "code": 1})).resolve(
        "6831725321", context=ResolverContext(environment={"FLICKR_API_KEY": "secret"})
    )
    assert result.status == "resolved_page_only"
