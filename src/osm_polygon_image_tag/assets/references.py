import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_TARGET_KEYS = (
    "image",
    "wikimedia_commons",
    "mapillary",
    "panoramax",
    "kartaview",
    "flickr",
    "bubbleid",
)


@dataclass(frozen=True, slots=True)
class SourceReference:
    provider: str
    source_tag_key: str
    source_tag_value: str
    canonical_reference: str
    resolver_kind: str


def _string_mapping(value: object) -> dict[str, str]:
    if isinstance(value, Mapping):
        return {
            str(key): str(item)
            for key, item in value.items()
            if key is not None and item is not None
        }
    if isinstance(value, list):
        return {
            str(key): str(item)
            for pair in value
            if isinstance(pair, tuple | list) and len(pair) == 2
            for key, item in (pair,)
        }
    return {}


def _ascii_decimal(value: str) -> bool:
    return value.isascii() and value.isdigit()


def _commons(value: str) -> tuple[str, str]:
    if value.startswith("Category:") and value.removeprefix("Category:"):
        return value.removeprefix("Category:"), "commons_category"
    if value.startswith(("File:", "Image:")):
        title = value.split(":", maxsplit=1)[1]
        if title:
            return f"File:{title}", "commons_file"
    return value, "invalid"


def _flickr(value: str) -> tuple[str, str]:
    if _ascii_decimal(value):
        return value, "flickr"
    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"flickr.com", "www.flickr.com"}
        and len(parts) >= 3
        and parts[0] == "photos"
        and _ascii_decimal(parts[2])
    ):
        return parts[2], "flickr"
    return value, "invalid"


def _canonical(key: str, raw_value: str) -> tuple[str, str, str]:
    value = raw_value.strip()
    if key == "wikimedia_commons":
        canonical, kind = _commons(value)
        return key, canonical, kind
    if key == "image":
        if value.startswith(("Category:", "File:", "Image:")):
            canonical, kind = _commons(value)
            return "wikimedia_commons", canonical, kind
        parsed = urlparse(value)
        kind = "generic_http" if parsed.scheme in {"http", "https"} and parsed.netloc else "invalid"
        return "image", value, kind
    if key.startswith("panoramax"):
        kind = "panoramax" if _UUID.fullmatch(value) else "invalid"
        return "panoramax", value.lower() if kind == "panoramax" else value, kind
    if key == "mapillary":
        return key, value, "mapillary" if _ascii_decimal(value) else "invalid"
    if key == "kartaview":
        parts = value.split("/")
        valid = len(parts) == 2 and all(_ascii_decimal(part) for part in parts)
        return key, value, "kartaview" if valid else "invalid"
    if key == "flickr":
        canonical, kind = _flickr(value)
        return key, canonical, kind
    if key == "bubbleid":
        return "streetside", value, "streetside" if _ascii_decimal(value) else "invalid"
    return key, value, "invalid"


def _reference(key: str, raw_value: str) -> SourceReference | None:
    if not raw_value:
        return None
    provider, canonical, kind = _canonical(key, raw_value)
    return SourceReference(provider, key, raw_value, canonical, kind)


def references_from_row(row: Mapping[str, Any]) -> tuple[SourceReference, ...]:
    tags = _string_mapping(row.get("tags"))
    panoramax = _string_mapping(row.get("panoramax_values"))
    references: list[SourceReference] = []
    for key in _TARGET_KEYS:
        if key == "panoramax" and panoramax:
            continue
        value = tags.get(key)
        if value is None and isinstance(row.get(key), str):
            value = row[key]
        if value is not None and (reference := _reference(key, value)) is not None:
            references.append(reference)
    for key, value in sorted(panoramax.items()):
        if (reference := _reference(key, value)) is not None:
            references.append(reference)
    return tuple(references)
