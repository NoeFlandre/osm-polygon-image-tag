import json
from collections.abc import Awaitable, Callable, Sequence

import httpx
import pytest

from osm_polygon_image_tag.resolvers.http import (
    ProviderRateLimited,
    ResponseTooLarge,
    SafeHttpClient,
    SafeHttpError,
    UnsafeUrlError,
    validate_public_url,
)
from osm_polygon_image_tag.resolvers.pinned_transport import PinnedNetworkBackend


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:password@example.test/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/",
        "http://10.0.0.1/",
        "http://192.0.2.1/",
    ],
)
def test_url_policy_rejects_unsafe_targets(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_public_url(url, ("93.184.216.34",))


def test_url_policy_rejects_mixed_public_and_private_dns_answers() -> None:
    with pytest.raises(UnsafeUrlError, match="non-public"):
        validate_public_url("https://example.test/", ("93.184.216.34", "10.0.0.1"))


def _resolver(
    answers: dict[str, Sequence[str]],
) -> Callable[[str], Awaitable[Sequence[str]]]:
    async def resolve(host: str) -> Sequence[str]:
        return answers[host]

    return resolve


@pytest.mark.asyncio
async def test_network_backend_connects_to_validated_ip_without_reresolving() -> None:
    calls: list[str] = []

    class Backend:
        async def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: object = None,
        ) -> object:
            calls.append(host)
            return object()

        async def sleep(self, _seconds: float) -> None:
            return None

    answers = iter([("93.184.216.34",), ("10.0.0.1",)])

    async def changing_dns(_host: str) -> Sequence[str]:
        return next(answers)

    backend = PinnedNetworkBackend(resolve=changing_dns, backend=Backend())

    stream = await backend.connect_tcp("example.test", 443)

    assert stream is not None
    assert calls == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_redirect_is_revalidated_before_following() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    client = SafeHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        resolve=_resolver({"example.test": ("93.184.216.34",)}),
    )

    with pytest.raises(UnsafeUrlError):
        await client.get_json("https://example.test/start")
    await client.aclose()


@pytest.mark.asyncio
async def test_redirect_loop_is_bounded() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": str(request.url)})

    client = SafeHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        resolve=_resolver({"example.test": ("93.184.216.34",)}),
        max_redirects=2,
    )

    with pytest.raises(SafeHttpError, match="too many redirects"):
        await client.get_json("https://example.test/start")
    await client.aclose()


@pytest.mark.asyncio
async def test_json_body_and_headers_are_bounded() -> None:
    async def body_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps({"data": "x" * 100}))

    body_client = SafeHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(body_handler)),
        resolve=_resolver({"example.test": ("93.184.216.34",)}),
        max_metadata_bytes=32,
    )
    with pytest.raises(ResponseTooLarge, match="body"):
        await body_client.get_json("https://example.test/data")
    await body_client.aclose()

    async def header_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"x-large": "x" * 100}, json={})

    header_client = SafeHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(header_handler)),
        resolve=_resolver({"example.test": ("93.184.216.34",)}),
        max_header_bytes=32,
    )
    with pytest.raises(ResponseTooLarge, match="headers"):
        await header_client.get_json("https://example.test/data")
    await header_client.aclose()


@pytest.mark.asyncio
async def test_timeout_error_redacts_query_and_credentials() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret=provider-token", request=request)

    client = SafeHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        resolve=_resolver({"example.test": ("93.184.216.34",)}),
    )

    with pytest.raises(SafeHttpError) as error:
        await client.get_json("https://example.test/data?access_token=user-secret")
    message = str(error.value)
    assert "user-secret" not in message
    assert "provider-token" not in message
    assert "example.test" in message
    await client.aclose()


@pytest.mark.asyncio
async def test_valid_json_is_returned_without_implicit_redirects() -> None:
    seen: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    client = SafeHttpClient(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(handle),
            follow_redirects=False,
        ),
        resolve=_resolver({"example.test": ("93.184.216.34",)}),
    )

    assert await client.get_json("https://example.test/data") == {"ok": True}
    assert seen == ["https://example.test/data"]
    await client.aclose()


@pytest.mark.asyncio
async def test_retry_after_is_exposed_as_structured_cooldown() -> None:
    async def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "120"}, json={"error": "slow down"})

    client = SafeHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        resolve=_resolver({"example.test": ("93.184.216.34",)}),
    )

    with pytest.raises(ProviderRateLimited) as error:
        await client.get_json("https://example.test/data")

    assert error.value.retry_after_seconds == 120
    await client.aclose()
