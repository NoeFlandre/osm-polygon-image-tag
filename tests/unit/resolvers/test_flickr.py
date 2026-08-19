from urllib.parse import parse_qs, urlparse

import pytest

from osm_polygon_image_tag.resolvers.flickr import FlickrResolver, _dimension, _largest_size
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
    assert result.reason == "missing_api_key"
    assert result.assets[0].provider_asset_id == "6831725321"
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
    query = parse_qs(urlparse(http.urls[0]).query)
    assert query == {
        "method": ["flickr.photos.getSizes"],
        "api_key": ["secret"],
        "photo_id": ["6831725321"],
        "format": ["json"],
        "nojsoncallback": ["1"],
    }
    assert result.assets[0].provider_asset_id == "6831725321"
    assert result.assets[0].page_url == "https://www.flickr.com/photo.gne?id=6831725321"
    assert result.assets[0].width == 4000
    assert result.assets[0].height == 2000


@pytest.mark.asyncio
async def test_flickr_permission_limited_response_is_page_only() -> None:
    result = await FlickrResolver(Http({"stat": "fail", "code": 1})).resolve(
        "6831725321", context=ResolverContext(environment={"FLICKR_API_KEY": "secret"})
    )
    assert result.status == "resolved_page_only"
    assert result.reason == "sizes_unavailable"
    assert result.assets[0].provider_asset_id == "6831725321"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"stat": "ok"},
        {"stat": "ok", "sizes": None},
        {"stat": "ok", "sizes": {"size": "not-a-list"}},
        {"stat": "ok", "sizes": {"size": [{"width": 100}]}},
        {"stat": "ok", "sizes": {"size": ["not-a-mapping"]}},
    ],
)
async def test_flickr_returns_page_only_for_unusable_sizes(payload: dict[str, object]) -> None:
    result = await FlickrResolver(Http(payload)).resolve(
        "6831725321", context=ResolverContext(environment={"FLICKR_API_KEY": "secret"})
    )

    assert result.status == "resolved_page_only"
    assert result.reason == "sizes_unavailable"
    assert result.assets[0].page_url == "https://www.flickr.com/photo.gne?id=6831725321"


@pytest.mark.asyncio
async def test_flickr_ignores_non_mapping_candidates_and_preserves_zero_dimensions() -> None:
    result = await FlickrResolver(
        Http(
            {
                "stat": "ok",
                "sizes": {
                    "size": [
                        "not-a-mapping",
                        {"source": "https://live.test/zero.jpg", "width": 0, "height": 0},
                    ]
                },
            }
        )
    ).resolve("6831725321", context=ResolverContext(environment={"FLICKR_API_KEY": "secret"}))

    assert result.status == "resolved"
    assert result.assets[0].image_url == "https://live.test/zero.jpg"
    assert result.assets[0].width is None
    assert result.assets[0].height is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (42, 42),
        (-2, -2),
        (True, 0),
        ("320", 320),
        ("", 0),
        ("3.2", 0),
        ("٣", 0),
        (None, 0),
    ],
)
def test_flickr_dimension_accepts_only_ascii_digit_sizes(value: object, expected: int) -> None:
    assert _dimension(value) == expected


def test_flickr_largest_size_returns_only_usable_candidates() -> None:
    assert _largest_size({"sizes": {"size": "not-a-list"}}) is None
    largest = _largest_size(
        {
            "sizes": {
                "size": [
                    {"source": "https://live.test/small.jpg", "width": 10, "height": 10},
                    {"source": "https://live.test/large.jpg", "width": 20, "height": 20},
                ]
            }
        }
    )
    assert largest is not None
    assert largest["source"] == "https://live.test/large.jpg"
