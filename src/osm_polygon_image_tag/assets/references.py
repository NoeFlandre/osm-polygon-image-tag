import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from osm_polygon_image_tag.core.contracts import IMAGE_REFERENCE_KEYS, PANORAMAX_VALUES_COLUMN

_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
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
        return _mapping_values(value.items())
    if isinstance(value, list):
        return _mapping_values(_iter_pairs(value))
    return {}


def _mapping_values(pairs: Iterable[tuple[object, object]]) -> dict[str, str]:
    return {str(key): str(item) for key, item in pairs if key is not None and item is not None}


def _iter_pairs(value: Iterable[object]) -> Iterable[tuple[object, object]]:
    for pair in value:
        parsed = _pair_values(pair)
        if parsed is None:
            continue
        yield parsed


def _pair_values(pair: object) -> tuple[object, object] | None:
    if isinstance(pair, Mapping):
        return pair.get("key"), pair.get("value")
    if isinstance(pair, tuple | list) and len(pair) == 2:
        return pair[0], pair[1]
    return None


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
    photo_id = _flickr_url_id(value)
    return (photo_id, "flickr") if photo_id is not None else (value, "invalid")


def _flickr_url_id(value: str) -> str | None:
    parsed = urlparse(value)
    if not _is_flickr_photo_url(parsed):
        return None
    return _flickr_path_id(_flickr_path_parts(parsed.path))


def _flickr_path_parts(path: str) -> list[str]:
    return [part for part in path.split("/") if part]


def _is_flickr_photo_url(parsed: object) -> bool:
    return getattr(parsed, "scheme", None) in {"http", "https"} and getattr(
        parsed, "hostname", None
    ) in {"flickr.com", "www.flickr.com"}


def _flickr_path_id(parts: list[str]) -> str | None:
    if not _valid_flickr_path(parts):
        return None
    return parts[2]


def _valid_flickr_path(parts: list[str]) -> bool:
    return len(parts) >= 3 and parts[0] == "photos" and _ascii_decimal(parts[2])


def _commons_reference(key: str, value: str) -> tuple[str, str, str]:
    canonical, kind = _commons(value)
    return key, canonical, kind


def _image_reference(value: str) -> tuple[str, str, str]:
    if value.startswith(("Category:", "File:", "Image:")):
        canonical, kind = _commons(value)
        return "wikimedia_commons", canonical, kind
    parsed = urlparse(value)
    kind = "generic_http" if parsed.scheme in {"http", "https"} and parsed.netloc else "invalid"
    return "image", value, kind


def _panoramax_reference(value: str) -> tuple[str, str, str]:
    kind = "panoramax" if _UUID.fullmatch(value) else "invalid"
    return "panoramax", value.lower() if kind == "panoramax" else value, kind


def _mapillary_reference(value: str) -> tuple[str, str, str]:
    return "mapillary", value, "mapillary" if _ascii_decimal(value) else "invalid"


def _kartaview_reference(value: str) -> tuple[str, str, str]:
    parts = value.split("/")
    valid = len(parts) == 2 and all(_ascii_decimal(part) for part in parts)
    return "kartaview", value, "kartaview" if valid else "invalid"


def _flickr_reference(value: str) -> tuple[str, str, str]:
    canonical, kind = _flickr(value)
    return "flickr", canonical, kind


def _streetside_reference(value: str) -> tuple[str, str, str]:
    return "streetside", value, "streetside" if _ascii_decimal(value) else "invalid"


_CANONICALIZERS: dict[str, Callable[[str], tuple[str, str, str]]] = {
    "wikimedia_commons": lambda value: _commons_reference("wikimedia_commons", value),
    "image": _image_reference,
    "mapillary": _mapillary_reference,
    "kartaview": _kartaview_reference,
    "flickr": _flickr_reference,
    "bubbleid": _streetside_reference,
}


def _canonical(key: str, raw_value: str) -> tuple[str, str, str]:
    value = raw_value.strip()
    if key.startswith("panoramax"):
        return _panoramax_reference(value)
    canonicalizer = _CANONICALIZERS.get(key)
    return canonicalizer(value) if canonicalizer is not None else (key, value, "invalid")


def _reference(key: str, raw_value: str) -> SourceReference | None:
    if not raw_value:
        return None
    provider, canonical, kind = _canonical(key, raw_value)
    return SourceReference(provider, key, raw_value, canonical, kind)


def references_from_row(row: Mapping[str, Any]) -> tuple[SourceReference, ...]:
    tags = _string_mapping(row.get("tags"))
    panoramax = _string_mapping(row.get(PANORAMAX_VALUES_COLUMN))
    return tuple(_row_references(row, tags, panoramax))


def _row_references(
    row: Mapping[str, Any], tags: Mapping[str, str], panoramax: Mapping[str, str]
) -> Iterable[SourceReference]:
    yield from _tag_references(row, tags, panoramax)
    yield from _panoramax_references(panoramax)


def _tag_references(
    row: Mapping[str, Any], tags: Mapping[str, str], panoramax: Mapping[str, str]
) -> Iterable[SourceReference]:
    for key in IMAGE_REFERENCE_KEYS:
        if _skip_primary_panoramax(key, panoramax):
            continue
        value = _tag_value(row, tags, key)
        reference = _reference_for_tag(key, value)
        if reference is not None:
            yield reference


def _skip_primary_panoramax(key: str, panoramax: Mapping[str, str]) -> bool:
    return key == "panoramax" and bool(panoramax)


def _reference_for_tag(key: str, value: str | None) -> SourceReference | None:
    return _reference(key, value) if value is not None else None


def _panoramax_references(panoramax: Mapping[str, str]) -> Iterable[SourceReference]:
    for key, value in sorted(panoramax.items()):
        reference = _reference(key, value)
        if reference is not None:
            yield reference


def _tag_value(row: Mapping[str, Any], tags: Mapping[str, str], key: str) -> str | None:
    value = tags.get(key)
    if value is None and isinstance(row.get(key), str):
        value = row[key]
    return value
