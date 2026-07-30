import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from urllib.parse import parse_qsl, urlparse

from osm_polygon_image_tag.assets.schema import validate_status
from osm_polygon_image_tag.core.errors import ImageTagPipelineError

_SECRET_QUERY_KEYS = frozenset({"access_token", "api_key", "token", "key"})


class ResolutionCacheError(ImageTagPipelineError):
    """Raised when durable resolution state is unsafe or inconsistent."""


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


def canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def record_payload(record: ResolutionRecord) -> dict[str, object]:
    payload = asdict(record)
    retry_after = record.retry_after
    payload["retry_after"] = retry_after.isoformat() if retry_after is not None else None
    return payload


def canonical_record_bytes(record: ResolutionRecord) -> bytes:
    return canonical_json_bytes(record_payload(record))


def validate_resolution_record(record: ResolutionRecord) -> None:
    try:
        validate_status(record.status)
    except ValueError as error:
        raise ResolutionCacheError(str(error)) from error
    parsed = urlparse(record.canonical_reference)
    query_keys = {key.lower() for key, _value in parse_qsl(parsed.query)}
    if query_keys & _SECRET_QUERY_KEYS:
        raise ResolutionCacheError("secret-bearing canonical references are not cacheable")
    if record.retry_after is not None and record.retry_after.tzinfo is None:
        raise ResolutionCacheError("retry_after must be timezone-aware")
