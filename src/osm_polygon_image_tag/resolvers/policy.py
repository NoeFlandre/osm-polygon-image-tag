import ipaddress
from collections.abc import Sequence
from urllib.parse import ParseResult, urlparse

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


class ProviderNotFound(SafeHttpError):
    """Provider reports that the requested asset does not exist."""


class ProviderAccessDenied(SafeHttpError):
    """Provider requires authorization or the asset is private."""


def validate_public_url(url: str, addresses: Sequence[str]) -> None:
    parsed = _parse_public_url(url)
    _validate_url_shape(parsed)
    candidates = _candidate_addresses(parsed.hostname, addresses)
    _validate_addresses(candidates)


def _parse_public_url(url: str) -> ParseResult:
    try:
        return urlparse(url)
    except ValueError as error:
        raise UnsafeUrlError("invalid URL") from error


def _validate_url_shape(parsed: ParseResult) -> None:
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUrlError("only absolute HTTP(S) URLs are allowed")
    _validate_credentials(parsed)
    _validate_port(parsed)


def _validate_credentials(parsed: ParseResult) -> None:
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("URL credentials are forbidden")


def _validate_port(parsed: ParseResult) -> None:
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise UnsafeUrlError("invalid URL port")


def _candidate_addresses(hostname: str | None, addresses: Sequence[str]) -> list[str]:
    candidates = list(addresses)
    try:
        literal = ipaddress.ip_address(hostname) if hostname else None
    except ValueError:
        literal = None
    if literal is not None:
        candidates.append(str(literal))
    if not candidates:
        raise UnsafeUrlError("host has no DNS answers")
    return candidates


def _validate_addresses(candidates: Sequence[str]) -> None:
    try:
        unsafe = [address for address in candidates if not ipaddress.ip_address(address).is_global]
    except ValueError as error:
        raise UnsafeUrlError("host returned an invalid IP address") from error
    if unsafe:
        raise UnsafeUrlError("host resolved to a non-public IP address")
