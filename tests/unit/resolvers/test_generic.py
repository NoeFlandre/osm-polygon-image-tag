import pytest

from osm_polygon_image_tag.resolvers.generic import GenericImageResolver
from osm_polygon_image_tag.resolvers.types import ImageProbe, ResolverContext


class Http:
    def __init__(self, probe: ImageProbe) -> None:
        self.probe = probe

    async def probe_image(self, url: str) -> ImageProbe:
        del url
        return self.probe


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mime", "status"),
    [
        ("image/jpeg", "resolved"),
        ("text/html; charset=utf-8", "not_direct_image"),
        ("video/mp4", "not_direct_image"),
        (None, "not_direct_image"),
    ],
)
async def test_generic_resolution_uses_returned_mime(mime: str | None, status: str) -> None:
    result = await GenericImageResolver(
        Http(ImageProbe("https://example.test/image", 200, mime, 100))
    ).resolve("https://example.test/image", context=ResolverContext())

    assert result.status == status
    if status == "resolved":
        assert result.assets[0].image_url == "https://example.test/image"
