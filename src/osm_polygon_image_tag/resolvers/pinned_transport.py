import socket
from collections.abc import Iterable
from typing import Any

import httpcore
import httpx

from osm_polygon_image_tag.resolvers.policy import validate_public_url
from osm_polygon_image_tag.resolvers.types import HostResolver

type SocketOption = (
    tuple[int, int, int] | tuple[int, int, bytes | bytearray] | tuple[int, int, None, int]
)


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        *,
        resolve: HostResolver,
        backend: httpcore.AsyncNetworkBackend | Any | None = None,
    ) -> None:
        self._resolve = resolve
        self._backend: Any = backend or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await self._resolve(host)
        scheme = "https" if port == 443 else "http"
        validate_public_url(f"{scheme}://{host}:{port}/", addresses)
        return await self._backend.connect_tcp(
            addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise RuntimeError("Unix sockets are not supported")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _ResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: Any) -> None:
        self._stream = stream

    async def __aiter__(self) -> Any:
        async for part in self._stream:
            yield part

    async def aclose(self) -> None:
        await self._stream.aclose()


class PinnedAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, resolve: HostResolver) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            network_backend=PinnedNetworkBackend(resolve=resolve),
            max_connections=16,
            max_keepalive_connections=8,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._pool.handle_async_request(
            httpcore.Request(
                method=request.method,
                url=httpcore.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=request.extensions,
            )
        )
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_ResponseStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


async def system_resolve(host: str) -> tuple[str, ...]:
    loop = __import__("asyncio").get_running_loop()
    info = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(str(item[4][0]) for item in info))
