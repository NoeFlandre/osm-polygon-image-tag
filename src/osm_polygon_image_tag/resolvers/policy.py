import ipaddress
from collections.abc import Sequence
from urllib.parse import urlparse

from osm_polygon_image_tag.core.errors import ImageTagPipelineError


class SafeHttpError(ImageTagPipelineError):
    """Base error for bounded provider HTTP operations."""


class UnsafeUrlError(SafeHttpError):
    """Raised before a request can reach an unsafe target."""


class ResponseTooLarge(SafeHttpError):
    """Raised when bounded provider metadata exceeds its limit."""


class ProviderRateLimited(SafeHttpError):
    def __init__(self, retry_after_seconds: int | None) -> None:
        super().__init__("provider rate limit reached")
        self.retry_after_seconds = retry_after_seconds


def validate_public_url(url: str, addresses: Sequence[str]) -> None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as error:
        raise UnsafeUrlError("invalid URL") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUrlError("only absolute HTTP(S) URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("URL credentials are forbidden")
    if port is not None and not 1 <= port <= 65535:
        raise UnsafeUrlError("invalid URL port")
    candidates = list(addresses)
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal = None
    if literal is not None:
        candidates.append(str(literal))
    if not candidates:
        raise UnsafeUrlError("host has no DNS answers")
    try:
        unsafe = [address for address in candidates if not ipaddress.ip_address(address).is_global]
    except ValueError as error:
        raise UnsafeUrlError("host returned an invalid IP address") from error
    if unsafe:
        raise UnsafeUrlError("host resolved to a non-public IP address")
