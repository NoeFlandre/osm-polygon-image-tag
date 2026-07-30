import pytest

from osm_polygon_image_tag.resolvers.kartaview import KartaViewResolver
from osm_polygon_image_tag.resolvers.types import ResolverContext


class Http:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.urls: list[str] = []

    async def get_json(self, url: str, **_kwargs: object) -> dict[str, object]:
        self.urls.append(url)
        return self.payload


@pytest.mark.asyncio
async def test_kartaview_uses_sequence_photo_endpoint_and_returned_url() -> None:
    http = Http(
        {
            "status": {"apiCode": 600, "httpCode": 200},
            "result": {
                "data": [
                    {
                        "id": "photo-1",
                        "fileurlProc": "https://storage.test/photo.jpg",
                        "fileurlLTh": "https://storage.test/thumb.jpg",
                    }
                ]
            },
        }
    )

    result = await KartaViewResolver(http).resolve("3936361/0", context=ResolverContext())

    assert "sequenceId=3936361" in http.urls[0]
    assert "sequenceIndex=0" in http.urls[0]
    assert result.status == "resolved"
    assert result.assets[0].image_url == "https://storage.test/photo.jpg"
    assert result.assets[0].page_url == ("https://kartaview.org/details/3936361/0/track-info")


@pytest.mark.asyncio
async def test_kartaview_http_200_internal_error_is_not_resolved() -> None:
    result = await KartaViewResolver(
        Http({"status": {"apiCode": 601, "httpCode": 200, "apiMessage": "missing"}})
    ).resolve("3936361/0", context=ResolverContext())

    assert result.status == "not_found"
