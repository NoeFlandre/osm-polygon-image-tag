from collections.abc import Mapping
from dataclasses import dataclass

from osm_polygon_image_tag.resolvers.commons import CommonsResolver
from osm_polygon_image_tag.resolvers.flickr import FlickrResolver
from osm_polygon_image_tag.resolvers.generic import GenericImageResolver
from osm_polygon_image_tag.resolvers.http import SafeHttpClient
from osm_polygon_image_tag.resolvers.kartaview import KartaViewResolver
from osm_polygon_image_tag.resolvers.mapillary import MapillaryResolver
from osm_polygon_image_tag.resolvers.panoramax import PanoramaxResolver
from osm_polygon_image_tag.resolvers.streetside import StreetsideResolver
from osm_polygon_image_tag.resolvers.types import Resolver


@dataclass(frozen=True, slots=True)
class ProviderLimit:
    max_concurrency: int
    requests_per_second: float


_LIMITS = {
    "wikimedia_commons": ProviderLimit(4, 2.0),
    "panoramax": ProviderLimit(8, 4.0),
    "mapillary": ProviderLimit(4, 2.0),
    "kartaview": ProviderLimit(2, 1.0),
    "flickr": ProviderLimit(2, 1.0),
    "streetside": ProviderLimit(8, 10.0),
    "image": ProviderLimit(4, 2.0),
}


class ResolverRegistry:
    def __init__(
        self,
        resolvers: Mapping[str, Resolver],
        *,
        environment: Mapping[str, str],
        http: SafeHttpClient,
    ) -> None:
        self._resolvers = dict(resolvers)
        self.environment = dict(environment)
        self._http = http

    @classmethod
    def build(
        cls,
        *,
        environment: Mapping[str, str],
        http: SafeHttpClient | None = None,
    ) -> "ResolverRegistry":
        client = http or SafeHttpClient()
        commons = CommonsResolver(client)
        resolvers: dict[str, Resolver] = {
            "commons_category": commons,
            "commons_file": commons,
            "panoramax": PanoramaxResolver(client),
            "mapillary": MapillaryResolver(client),
            "kartaview": KartaViewResolver(client),
            "flickr": FlickrResolver(client),
            "streetside": StreetsideResolver(),
            "generic_http": GenericImageResolver(client),
        }
        return cls(resolvers, environment=environment, http=client)

    def resolver_for(self, kind: str) -> Resolver:
        return self._resolvers[kind]

    def limit_for(self, provider: str) -> ProviderLimit:
        return _LIMITS[provider]

    def capability(self, provider: str) -> str:
        if provider == "mapillary" and "MAPILLARY_ACCESS_TOKEN" not in self.environment:
            return "page_only_missing_token"
        if provider == "flickr" and "FLICKR_API_KEY" not in self.environment:
            return "page_only_missing_key"
        if provider == "streetside":
            return "page_only"
        return "direct"

    async def aclose(self) -> None:
        await self._http.aclose()
