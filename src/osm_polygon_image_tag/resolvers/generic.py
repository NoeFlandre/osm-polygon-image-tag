from typing import Protocol

from osm_polygon_image_tag.resolvers.types import (
    ImageProbe,
    ResolutionResult,
    ResolvedAsset,
    ResolverContext,
)


class ProbeClient(Protocol):
    async def probe_image(self, url: str) -> ImageProbe: ...


class GenericImageResolver:
    provider = "image"

    def __init__(self, http: ProbeClient) -> None:
        self._http = http

    async def resolve(
        self, canonical_reference: str, *, context: ResolverContext
    ) -> ResolutionResult:
        del context
        probe = await self._http.probe_image(canonical_reference)
        if probe.status_code >= 400:
            return ResolutionResult(status="not_found", reason="http_status")
        if probe.mime_type is None or not probe.mime_type.lower().startswith("image/"):
            return ResolutionResult(status="not_direct_image", reason="non_image_mime")
        return ResolutionResult(
            status="resolved",
            assets=(
                ResolvedAsset(
                    page_url=canonical_reference,
                    image_url=probe.final_url,
                    mime_type=probe.mime_type.split(";", maxsplit=1)[0].lower(),
                ),
            ),
        )
