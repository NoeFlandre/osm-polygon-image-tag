from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from osm_polygon_image_tag.resolvers.mapillary import MapillaryResolver, _expiry
from osm_polygon_image_tag.resolvers.types import ResolverContext


class Http:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.urls: list[str] = []
        self.headers: list[Mapping[str, str] | None] = []

    async def get_json(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> dict[str, object]:
        self.urls.append(url)
        self.headers.append(headers)
        return self.payload


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (None, None),
        ("https://cdn.test/image.jpg?Expires=not-a-number", None),
        ("https://cdn.test/image.jpg?Expires=é", None),
    ],
)
def test_mapillary_expiry_accepts_only_ascii_epoch_query_values(
    url: str | None, expected: datetime | None
) -> None:
    assert _expiry(url) == expected


@pytest.mark.asyncio
async def test_mapillary_without_token_is_factual_page_only() -> None:
    http = Http({})

    result = await MapillaryResolver(http).resolve(
        "2627502594079174", context=ResolverContext(environment={})
    )

    assert result.status == "resolved_page_only"
    assert result.assets[0].page_url == ("https://www.mapillary.com/app/?pKey=2627502594079174")
    assert result.assets[0].image_url is None
    assert http.urls == []


@pytest.mark.asyncio
async def test_mapillary_token_resolves_returned_original_thumbnail_and_expiry() -> None:
    expires = 1_800_000_000
    http = Http(
        {
            "id": "2627502594079174",
            "thumb_original_url": f"https://scontent.test/image.jpg?Expires={expires}",
            "thumb_2048_url": "https://scontent.test/thumb.jpg",
            "width": 4000,
            "height": 2000,
        }
    )

    result = await MapillaryResolver(http).resolve(
        "2627502594079174",
        context=ResolverContext(environment={"MAPILLARY_ACCESS_TOKEN": "secret"}),
    )

    assert result.status == "resolved"
    asset = result.assets[0]
    assert asset.image_url == f"https://scontent.test/image.jpg?Expires={expires}"
    assert asset.thumbnail_url == "https://scontent.test/thumb.jpg"
    assert asset.image_url_expires_at == datetime.fromtimestamp(expires, tz=UTC)
    assert parse_qs(urlparse(http.urls[0]).query)["fields"]
    assert "secret" not in http.urls[0]
    assert http.headers == [{"Authorization": "OAuth secret"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"error": {"code": 100, "message": "Unsupported get request"}}, "not_found"),
        ({"error": {"code": 190, "message": "Invalid token"}}, "requires_auth"),
    ],
)
async def test_mapillary_errors_have_finite_statuses(
    payload: dict[str, object], status: str
) -> None:
    result = await MapillaryResolver(Http(payload)).resolve(
        "2627502594079174",
        context=ResolverContext(environment={"MAPILLARY_ACCESS_TOKEN": "secret"}),
    )
    assert result.status == status
