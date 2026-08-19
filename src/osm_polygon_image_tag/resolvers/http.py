import asyncio
import ipaddress
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import NoReturn
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

import httpcore
import httpx

from osm_polygon_image_tag.resolvers.pinned_transport import PinnedAsyncTransport, system_resolve
from osm_polygon_image_tag.resolvers.policy import (
    ProviderAccessDenied,
    ProviderNotFound,
    ProviderRateLimited,
    ResponseTooLarge,
    SafeHttpError,
    UnsafeUrlError,
    validate_public_url,
)
from osm_polygon_image_tag.resolvers.types import HostResolver, ImageProbe

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})
_MAX_RETRY_AFTER_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class _JsonAttempt:
    payload: Mapping[str, object] | None
    next_url: str | None
    headers: dict[str, str]
    origin: tuple[str, str | None, int | None]


def _redacted_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _unsafe_url_cause(error: BaseException) -> UnsafeUrlError | None:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, UnsafeUrlError):
            return current
        current = current.__cause__ or current.__context__
    return None


def _header_size(headers: httpx.Headers) -> int:
    return sum(len(key.encode()) + len(value.encode()) for key, value in headers.multi_items())


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname, port


def _validate_json_shape(payload: object) -> None:
    stack = [(payload, 0)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        _validate_json_limits(depth, nodes)
        stack.extend(_json_children(value, depth))


def _validate_json_limits(depth: int, nodes: int) -> None:
    if depth > 64:
        raise ResponseTooLarge("provider JSON nesting exceeds limit")
    if nodes > 100_000:
        raise ResponseTooLarge("provider JSON element count exceeds limit")


def _json_children(value: object, depth: int) -> list[tuple[object, int]]:
    if isinstance(value, dict):
        return [(item, depth + 1) for item in value.values()]
    if isinstance(value, list):
        return [(item, depth + 1) for item in value]
    return []


def _json_redirect(
    response: httpx.Response,
    current: str,
    origin: tuple[str, str | None, int | None],
    headers: dict[str, str],
) -> _JsonAttempt | None:
    if response.status_code not in _REDIRECTS:
        return None
    location = response.headers.get("location")
    if location is None:
        raise SafeHttpError("redirect response has no location")
    next_url = urljoin(current, location)
    next_origin = _origin(next_url)
    _validate_redirect_origin(origin, next_origin)
    next_headers = _redirect_headers(headers, origin, next_origin)
    return _JsonAttempt(None, next_url, next_headers, next_origin)


def _validate_redirect_origin(
    origin: tuple[str, str | None, int | None],
    next_origin: tuple[str, str | None, int | None],
) -> None:
    if origin[0] == "https" and next_origin[0] != "https":
        raise UnsafeUrlError("HTTPS redirect downgrade is forbidden")


def _redirect_headers(
    headers: dict[str, str],
    origin: tuple[str, str | None, int | None],
    next_origin: tuple[str, str | None, int | None],
) -> dict[str, str]:
    if next_origin == origin:
        return headers
    return _strip_sensitive_headers(headers)


def _strip_sensitive_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() not in _SENSITIVE_HEADERS}


def _raise_json_status(response: httpx.Response) -> None:
    if response.status_code == 429:
        raise ProviderRateLimited(_retry_after_seconds(response.headers.get("retry-after", "")))
    if response.status_code == 404:
        raise ProviderNotFound("provider asset not found")
    if response.status_code in {401, 403}:
        raise ProviderAccessDenied("provider access denied")
    response.raise_for_status()


def _retry_after_seconds(value: str) -> int | None:
    if not value.isascii() or not value.isdigit():
        return None
    return min(int(value), _MAX_RETRY_AFTER_SECONDS)


def _raise_safe_request_error(error: BaseException, current: str) -> NoReturn:
    unsafe = _unsafe_url_cause(error)
    if unsafe is not None:
        raise unsafe from error
    raise SafeHttpError(f"provider request failed: {_redacted_url(current)}") from error


def _probe_redirect(current: str, location: str | None) -> tuple[str, None]:
    if location is None:
        raise SafeHttpError("redirect response has no location")
    return urljoin(current, location), None


def _content_length(value: str) -> int | None:
    return int(value) if value.isdigit() else None


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
        self._total_timeout = timeout_seconds
        self._client = client or httpx.AsyncClient(
            transport=PinnedAsyncTransport(resolve=resolve),
            follow_redirects=False,
            timeout=timeout_seconds,
            trust_env=False,
        )

    async def _validate(self, url: str) -> None:
        parsed = urlparse(url)
        addresses = await self._resolved_addresses(parsed.hostname)
        validate_public_url(url, addresses)

    async def _resolved_addresses(self, hostname: str | None) -> tuple[str, ...]:
        if hostname is None:
            return ()
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None:
            return ()
        try:
            return tuple(await self._resolve(hostname))
        except OSError as error:
            raise SafeHttpError(f"DNS resolution failed: {hostname}") from error

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
        try:
            async with asyncio.timeout(self._total_timeout):
                return await self._get_json(url, headers=headers)
        except TimeoutError as error:
            raise SafeHttpError(f"provider request timed out: {_redacted_url(url)}") from error

    async def _get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None,
    ) -> Mapping[str, object]:
        current = url
        request_headers = dict(headers or {})
        origin = _origin(current)
        for _redirect_count in range(self._max_redirects + 1):
            await self._validate(current)
            attempt = await self._safe_request_json(current, request_headers, origin)
            if attempt.next_url is not None:
                current, request_headers, origin = (
                    attempt.next_url,
                    attempt.headers,
                    attempt.origin,
                )
                continue
            if attempt.payload is None:
                raise SafeHttpError("provider JSON response is empty")
            return attempt.payload
        raise SafeHttpError("too many redirects")

    async def _safe_request_json(
        self,
        current: str,
        request_headers: dict[str, str],
        origin: tuple[str, str | None, int | None],
    ) -> _JsonAttempt:
        try:
            return await self._request_json_once(current, request_headers, origin)
        except (
            httpx.HTTPError,
            httpcore.NetworkError,
            httpcore.ProtocolError,
            httpcore.TimeoutException,
        ) as error:
            _raise_safe_request_error(error, current)
        except json.JSONDecodeError as error:
            raise SafeHttpError("provider returned invalid JSON") from error

    async def _request_json_once(
        self,
        current: str,
        request_headers: dict[str, str],
        origin: tuple[str, str | None, int | None],
    ) -> _JsonAttempt:
        async with self._client.stream("GET", current, headers=request_headers) as response:
            if _header_size(response.headers) > self._max_header_bytes:
                raise ResponseTooLarge("provider response headers exceed limit")
            redirect = _json_redirect(response, current, origin, request_headers)
            if redirect is not None:
                return redirect
            _raise_json_status(response)
            payload = json.loads(await self._body(response))
            if not isinstance(payload, dict):
                raise SafeHttpError("provider JSON must be an object")
            _validate_json_shape(payload)
            return _JsonAttempt(payload, None, request_headers, origin)

    async def probe_image(self, url: str) -> ImageProbe:
        try:
            async with asyncio.timeout(self._total_timeout):
                return await self._probe_image(url)
        except TimeoutError as error:
            raise SafeHttpError(f"provider request timed out: {_redacted_url(url)}") from error

    async def _probe_image(self, url: str) -> ImageProbe:
        current = url
        for _redirect_count in range(self._max_redirects + 1):
            await self._validate(current)
            try:
                next_url, probe = await self._probe_image_once(current)
                if probe is not None:
                    return probe
                current = next_url
            except (
                httpx.HTTPError,
                httpcore.NetworkError,
                httpcore.ProtocolError,
                httpcore.TimeoutException,
            ) as error:
                unsafe = _unsafe_url_cause(error)
                if unsafe is not None:
                    raise unsafe from error
                raise SafeHttpError(f"provider request failed: {_redacted_url(current)}") from error
        raise SafeHttpError("too many redirects")

    async def _probe_image_once(self, current: str) -> tuple[str, ImageProbe | None]:
        async with self._client.stream("GET", current, headers={"Range": "bytes=0-0"}) as response:
            if _header_size(response.headers) > self._max_header_bytes:
                raise ResponseTooLarge("provider response headers exceed limit")
            if response.status_code in _REDIRECTS:
                return _probe_redirect(current, response.headers.get("location"))
            content_length = _content_length(response.headers.get("content-length", ""))
            return current, ImageProbe(
                final_url=str(response.url),
                status_code=response.status_code,
                mime_type=response.headers.get("content-type"),
                content_length=content_length,
            )

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = [
    "ProviderAccessDenied",
    "ProviderNotFound",
    "ProviderRateLimited",
    "ResponseTooLarge",
    "SafeHttpClient",
    "SafeHttpError",
    "UnsafeUrlError",
    "validate_public_url",
]
