import pytest

from osm_polygon_image_tag.resolvers.streetside import StreetsideResolver
from osm_polygon_image_tag.resolvers.types import ResolverContext


@pytest.mark.asyncio
async def test_streetside_emits_id_viewer_page_without_raw_image() -> None:
    result = await StreetsideResolver().resolve(
        "718514589",
        context=ResolverContext(bbox=(4.35, 50.84, 4.37, 50.86)),
    )

    assert result.status == "resolved_page_only"
    asset = result.assets[0]
    assert asset.page_url == (
        "https://www.openstreetmap.org/edit?editor=id&lat=50.8500000&lon=4.3600000"
        "&zoom=19#photo=streetside/718514589&photo_overlay=streetside"
    )
    assert asset.image_url is None


@pytest.mark.asyncio
async def test_streetside_without_location_is_unsupported() -> None:
    result = await StreetsideResolver().resolve("718514589", context=ResolverContext(bbox=None))
    assert result.status == "unsupported"
