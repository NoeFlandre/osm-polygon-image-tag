from urllib.parse import urlencode

from osm_polygon_image_tag.resolvers.response import MetadataClient, as_mapping, as_text
from osm_polygon_image_tag.resolvers.types import (
    ResolutionResult,
    ResolvedAsset,
    ResolverContext,
)


class KartaViewResolver:
    provider = "kartaview"

    def __init__(self, http: MetadataClient) -> None:
        self._http = http

    async def resolve(
        self, canonical_reference: str, *, context: ResolverContext
    ) -> ResolutionResult:
        del context
        sequence_id, sequence_index = canonical_reference.split("/", maxsplit=1)
        query = urlencode({"sequenceId": sequence_id, "sequenceIndex": sequence_index})
        payload = await self._http.get_json(f"https://api.openstreetcam.org/2.0/photo/?{query}")
        status = as_mapping(payload.get("status"))
        if status.get("apiCode") != 600:
            return ResolutionResult(status="not_found", reason="kartaview_api_error")
        data = as_mapping(payload.get("result")).get("data")
        if not isinstance(data, list) or not data:
            return ResolutionResult(status="not_found", reason="kartaview_photo_missing")
        photo = as_mapping(data[0])
        image_url = as_text(photo.get("fileurlProc"))
        if image_url is None:
            return ResolutionResult(
                status="not_direct_image",
                reason="kartaview_returned_no_image",
            )
        return ResolutionResult(
            status="resolved",
            assets=(
                ResolvedAsset(
                    provider_asset_id=as_text(photo.get("id")),
                    page_url=(
                        f"https://kartaview.org/details/{sequence_id}/{sequence_index}/track-info"
                    ),
                    image_url=image_url,
                    thumbnail_url=as_text(photo.get("fileurlLTh")),
                ),
            ),
        )
