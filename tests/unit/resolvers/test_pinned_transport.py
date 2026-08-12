import asyncio
import socket
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpcore
import httpx
import pytest

from osm_polygon_image_tag.resolvers.pinned_transport import (
    PinnedAsyncTransport,
    PinnedNetworkBackend,
    _ResponseStream,
    system_resolve,
)
from osm_polygon_image_tag.resolvers.policy import UnsafeUrlError


@pytest.mark.asyncio
async def test_network_backend_pins_first_public_address_and_forwards_options() -> None:
    seen: dict[str, object] = {}
    marker = object()

    class Backend:
        async def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: object = None,
        ) -> object:
            seen.update(
                host=host,
                port=port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )
            return marker

        async def sleep(self, seconds: float) -> None:
            seen["sleep"] = seconds

    async def resolve(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34", "93.184.216.35")

    options = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    backend = PinnedNetworkBackend(resolve=resolve, backend=Backend())

    assert (
        await backend.connect_tcp(
            "example.test",
            443,
            timeout=1.5,
            local_address="192.0.2.10",
            socket_options=options,
        )
        is marker
    )
    await backend.sleep(0.25)

    assert seen == {
        "host": "93.184.216.34",
        "port": 443,
        "timeout": 1.5,
        "local_address": "192.0.2.10",
        "socket_options": options,
        "sleep": 0.25,
    }


@pytest.mark.parametrize(
    ("addresses", "message"),
    [((), "no DNS answers"), (("10.0.0.1",), "non-public")],
)
@pytest.mark.asyncio
async def test_network_backend_rejects_unsafe_dns_answers_before_connecting(
    addresses: tuple[str, ...],
    message: str,
) -> None:
    connected = False

    class Backend:
        async def connect_tcp(
            self,
            _host: str,
            _port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: object = None,
        ) -> object:
            nonlocal connected
            connected = True
            return object()

    async def resolve(_host: str) -> tuple[str, ...]:
        return addresses

    backend = PinnedNetworkBackend(resolve=resolve, backend=Backend())

    with pytest.raises(UnsafeUrlError, match=message):
        await backend.connect_tcp("example.test", 443)

    assert not connected


@pytest.mark.asyncio
async def test_network_backend_rejects_unix_sockets() -> None:
    async def resolve(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    backend = PinnedNetworkBackend(resolve=resolve, backend=object())

    with pytest.raises(RuntimeError, match="Unix sockets are not supported"):
        await backend.connect_unix_socket("provider.sock")


class _SourceStream:
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"first"
        yield b"second"

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_response_stream_forwards_chunks_and_close() -> None:
    source = _SourceStream()
    stream = _ResponseStream(source)

    assert [chunk async for chunk in stream] == [b"first", b"second"]
    await stream.aclose()

    assert source.closed


class _Pool:
    def __init__(self, stream: _SourceStream) -> None:
        self.request: httpcore.Request | None = None
        self.stream = stream
        self.closed = False

    async def handle_async_request(self, request: httpcore.Request) -> Any:
        self.request = request
        return SimpleNamespace(
            status=206,
            headers=[(b"content-type", b"image/jpeg")],
            stream=self.stream,
            extensions={"trace": "test"},
        )

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_transport_maps_request_response_stream_and_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    source = _SourceStream()
    pool = _Pool(source)
    monkeypatch.setattr(httpcore, "AsyncConnectionPool", lambda **_kwargs: pool)
    transport = PinnedAsyncTransport(resolve=resolve)
    request = httpx.Request(
        "POST",
        "https://example.test/image.jpg?size=small",
        headers={"X-Test": "yes"},
        content=b"payload",
    )

    response = await transport.handle_async_request(request)
    assert response.status_code == 206
    assert response.headers["content-type"] == "image/jpeg"
    assert response.extensions == {"trace": "test"}
    assert await response.aread() == b"firstsecond"
    await response.aclose()
    await transport.aclose()

    assert pool.request is not None
    assert pool.request.method == b"POST"
    assert pool.request.url.target == b"/image.jpg?size=small"
    assert (b"X-Test", b"yes") in pool.request.headers
    assert source.closed
    assert pool.closed


@pytest.mark.asyncio
async def test_system_resolve_deduplicates_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object, int]] = []

    class Loop:
        async def getaddrinfo(
            self,
            host: str,
            service: object,
            *,
            type: int,
        ) -> list[tuple[int, int, int, str, tuple[str, ...]]]:
            calls.append((host, service, type))
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", "0")),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", "0")),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", "0")),
            ]

    loop = Loop()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)

    assert await system_resolve("example.test") == (
        "93.184.216.34",
        "2001:db8::1",
    )
    assert calls == [("example.test", None, socket.SOCK_STREAM)]
