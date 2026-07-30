from osm_polygon_image_tag.resolvers.types import (
    ResolutionResult,
    ResolvedAsset,
    ResolverContext,
)


class StreetsideResolver:
    provider = "streetside"

    async def resolve(
        self, canonical_reference: str, *, context: ResolverContext
    ) -> ResolutionResult:
        if context.bbox is None:
            return ResolutionResult(
                status="unsupported",
                reason="polygon_location_missing",
            )
        min_lon, min_lat, max_lon, max_lat = context.bbox
        lon = (min_lon + max_lon) / 2
        lat = (min_lat + max_lat) / 2
        page_url = (
            "https://www.openstreetmap.org/edit?editor=id"
            f"&lat={lat:.7f}&lon={lon:.7f}&zoom=19"
            f"#photo=streetside/{canonical_reference}&photo_overlay=streetside"
        )
        return ResolutionResult(
            status="resolved_page_only",
            assets=(
                ResolvedAsset(
                    provider_asset_id=canonical_reference,
                    page_url=page_url,
                ),
            ),
            reason="provider_has_no_documented_raw_image_api",
        )
