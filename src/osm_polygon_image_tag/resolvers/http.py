import ipaddress
import json
from collections.abc import Mapping
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

import httpcore
import httpx

from osm_polygon_image_tag.resolvers.pinned_transport import PinnedAsyncTransport, system_resolve
from osm_polygon_image_tag.resolvers.policy import (
    ProviderRateLimited,
    ResponseTooLarge,
    SafeHttpError,
    UnsafeUrlError,
    validate_public_url,
)
from osm_polygon_image_tag.resolvers.types import HostResolver, ImageProbe

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})


def _redacted_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _header_size(headers: httpx.Headers) -> int:
    return sum(len(key.encode()) + len(value.encode()) for key, value in headers.multi_items())


class SafeHttpClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        resolve: HostResolver = system_resolve,
        max_metadata_bytes: int = 2 * 1024 * 1024,
        max_header_bytes: int = 64 * 1024,
        max_redirects: int = 5,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._resolve = resolve
        self._max_metadata_bytes = max_metadata_bytes
        self._max_header_bytes = max_header_bytes
        self._max_redirects = max_redirects
        self._client = client or httpx.AsyncClient(
            transport=PinnedAsyncTransport(resolve=resolve),
            follow_redirects=False,
            timeout=timeout_seconds,
            trust_env=False,
        )

    async def _validate(self, url: str) -> None:
        parsed = urlparse(url)
        try:
            literal = ipaddress.ip_address(parsed.hostname) if parsed.hostname else None
        except ValueError:
            literal = None
        addresses = (
            await self._resolve(parsed.hostname)
            if parsed.hostname is not None and literal is None
            else ()
        )
        validate_public_url(url, addresses)

    async def _body(self, response: httpx.Response) -> bytes:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > self._max_metadata_bytes:
                raise ResponseTooLarge("provider response body exceeds limit")
        return bytes(body)

    async def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, object]:
        current = url
        request_headers = dict(headers or {})
        origin = urlparse(current).netloc
        for _redirect_count in range(self._max_redirects + 1):
            await self._validate(current)
            try:
                async with self._client.stream("GET", current, headers=request_headers) as response:
                    if _header_size(response.headers) > self._max_header_bytes:
                        raise ResponseTooLarge("provider response headers exceed limit")
                    if response.status_code in _REDIRECTS:
                        location = response.headers.get("location")
                        if location is None:
                            raise SafeHttpError("redirect response has no location")
                        next_url = urljoin(current, location)
                        if urlparse(next_url).netloc != origin:
                            request_headers = {
                                key: value
                                for key, value in request_headers.items()
                                if key.lower() not in _SENSITIVE_HEADERS
                            }
                        current = next_url
                        origin = urlparse(current).netloc
                        continue
                    if response.status_code == 429:
                        retry_value = response.headers.get("retry-after", "")
                        raise ProviderRateLimited(
                            int(retry_value)
                            if retry_value.isascii() and retry_value.isdigit()
                            else None
                        )
                    response.raise_for_status()
                    payload = json.loads(await self._body(response))
            except (httpx.HTTPError, httpcore.NetworkError, httpcore.TimeoutException) as error:
                raise SafeHttpError(f"provider request failed: {_redacted_url(current)}") from error
            except json.JSONDecodeError as error:
                raise SafeHttpError("provider returned invalid JSON") from error
            if not isinstance(payload, dict):
                raise SafeHttpError("provider JSON must be an object")
            return payload
        raise SafeHttpError("too many redirects")

    async def probe_image(self, url: str) -> ImageProbe:
        await self._validate(url)
        try:
            async with self._client.stream("GET", url, headers={"Range": "bytes=0-0"}) as response:
                if _header_size(response.headers) > self._max_header_bytes:
                    raise ResponseTooLarge("provider response headers exceed limit")
                return ImageProbe(
                    final_url=str(response.url),
                    status_code=response.status_code,
                    mime_type=response.headers.get("content-type"),
                    content_length=int(response.headers["content-length"])
                    if response.headers.get("content-length", "").isdigit()
                    else None,
                )
        except (httpx.HTTPError, httpcore.NetworkError, httpcore.TimeoutException) as error:
            raise SafeHttpError(f"provider request failed: {_redacted_url(url)}") from error

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = [
    "ProviderRateLimited",
    "ResponseTooLarge",
    "SafeHttpClient",
    "SafeHttpError",
    "UnsafeUrlError",
    "validate_public_url",
]
