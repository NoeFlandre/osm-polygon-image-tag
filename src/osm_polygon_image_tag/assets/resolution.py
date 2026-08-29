import hashlib
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qsl, urlparse

from osm_polygon_image_tag.assets.schema import validate_status
from osm_polygon_image_tag.core.errors import ImageTagPipelineError
from osm_polygon_image_tag.core.serialization import canonical_json_bytes

_SECRET_QUERY_KEYS = frozenset({"access_token", "api_key", "token", "key"})


class ResolutionCacheError(ImageTagPipelineError):
    """Raised when durable resolution state is unsafe or inconsistent."""


def is_cacheable_canonical_reference(canonical_reference: str) -> bool:
    """Return whether a canonical reference is safe to persist durably.

    Source-provided URLs that carry secret-like query keys (``access_token``,
    ``api_key``, ``token``, ``key``) are treated as non-cacheable: they must
    never be written to the SQLite resolution cache or included in a
    resolution snapshot. Matching is case-insensitive and accounts for
    percent-encoding because :func:`urllib.parse.parse_qsl` decodes query keys.
    """
    parsed = urlparse(canonical_reference)
    query_keys = {key.lower() for key, _value in parse_qsl(parsed.query)}
    return not (query_keys & _SECRET_QUERY_KEYS)


@dataclass(frozen=True, slots=True)
class ResolutionKey:
    provider: str
    canonical_reference: str
    resolver_contract_version: int


@dataclass(frozen=True, slots=True)
class ResolutionRecord:
    provider: str
    canonical_reference: str
    resolver_contract_version: int
    status: str
    assets: tuple[dict[str, object], ...]
    retry_after: datetime | None
    reason: str | None = None
    category_truncated: bool = False
    attempt_count: int = 1

    @property
    def key(self) -> ResolutionKey:
        return ResolutionKey(
            self.provider,
            self.canonical_reference,
            self.resolver_contract_version,
        )

    @property
    def response_sha256(self) -> str:
        return hashlib.sha256(canonical_record_bytes(self)).hexdigest()


def record_payload(record: ResolutionRecord) -> dict[str, object]:
    return _record_payload(record, copy_assets=True)


def canonical_record_payload(record: ResolutionRecord) -> dict[str, object]:
    return _record_payload(record, copy_assets=False)


def _record_payload(record: ResolutionRecord, *, copy_assets: bool) -> dict[str, object]:
    assets = tuple(deepcopy(asset) for asset in record.assets) if copy_assets else record.assets
    return {
        "provider": record.provider,
        "canonical_reference": record.canonical_reference,
        "resolver_contract_version": record.resolver_contract_version,
        "status": record.status,
        "assets": assets,
        "retry_after": (record.retry_after.isoformat() if record.retry_after is not None else None),
        "reason": record.reason,
        "category_truncated": record.category_truncated,
        "attempt_count": record.attempt_count,
    }


def canonical_record_bytes(record: ResolutionRecord) -> bytes:
    return canonical_json_bytes(canonical_record_payload(record))


def validate_resolution_record(record: ResolutionRecord) -> None:
    try:
        validate_status(record.status)
    except ValueError as error:
        raise ResolutionCacheError(str(error)) from error
    _validate_record_reference(record)
    _validate_record_retry(record)
    _validate_attempt_count(record)


def _validate_record_reference(record: ResolutionRecord) -> None:
    if not is_cacheable_canonical_reference(record.canonical_reference):
        raise ResolutionCacheError("secret-bearing canonical references are not cacheable")


def _validate_record_retry(record: ResolutionRecord) -> None:
    if record.retry_after is not None and record.retry_after.tzinfo is None:
        raise ResolutionCacheError("retry_after must be timezone-aware")


def _validate_attempt_count(record: ResolutionRecord) -> None:
    if record.attempt_count < 0:
        raise ResolutionCacheError("attempt_count must not be negative")
