import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TARGET_TAG_KEYS = (
    "image",
    "wikimedia_commons",
    "mapillary",
    "panoramax",
    "kartaview",
    "flickr",
)

_COPY_ESCAPES = {
    ord("b"): b"\b",
    ord("f"): b"\f",
    ord("n"): b"\n",
    ord("r"): b"\r",
    ord("t"): b"\t",
    ord("v"): b"\v",
    ord("\\"): b"\\",
}


@dataclass(frozen=True, slots=True)
class ExportRecord:
    geometry_ewkb_hex: str
    osm_type: str
    osm_id: int
    version: int | None
    changeset: int | None
    timestamp: str | None
    tags: dict[str, str]


def export_command(
    pbf_path: Path,
    config_path: Path,
    *,
    executable: str = "osmium",
) -> tuple[str, ...]:
    return (
        executable,
        "export",
        str(pbf_path),
        "--output-format",
        "pg",
        "--config",
        str(config_path),
        "--geometry-types",
        "polygon",
        "--output",
        "-",
    )


def _decode_copy_field(field: bytes) -> str:
    decoded = bytearray()
    index = 0
    while index < len(field):
        value = field[index]
        if value != ord("\\"):
            decoded.append(value)
            index += 1
            continue
        index += 1
        if index >= len(field):
            raise ValueError("COPY field ends with an incomplete escape")
        escaped = field[index]
        decoded.extend(_COPY_ESCAPES.get(escaped, bytes((escaped,))))
        index += 1
    return decoded.decode("utf-8")


def _optional_text(field: bytes) -> str | None:
    return None if field == b"\\N" else _decode_copy_field(field)


def _optional_int(field: bytes, *, name: str) -> int | None:
    value = _optional_text(field)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer or null") from error


def _parse_tags(field: bytes) -> dict[str, str]:
    try:
        value: Any = json.loads(_decode_copy_field(field))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("tags must be valid UTF-8 JSON") from error
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError("tags must be a JSON object of string keys and values")
    return dict(value)


def parse_copy_record(line: bytes) -> ExportRecord:
    fields = line.rstrip(b"\r\n").split(b"\t")
    if len(fields) != 7:
        raise ValueError(f"expected 7 COPY fields, received {len(fields)}")
    geometry, osm_type, osm_id, version, changeset, timestamp, tags = fields
    try:
        required_id = int(_decode_copy_field(osm_id))
    except ValueError as error:
        raise ValueError("osm_id must be an integer") from error
    return ExportRecord(
        geometry_ewkb_hex=_decode_copy_field(geometry),
        osm_type=_decode_copy_field(osm_type),
        osm_id=required_id,
        version=_optional_int(version, name="version"),
        changeset=_optional_int(changeset, name="changeset"),
        timestamp=_optional_text(timestamp),
        tags=_parse_tags(tags),
    )


def iter_records(lines: Iterable[bytes]) -> Iterator[ExportRecord]:
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            yield parse_copy_record(line)
        except ValueError as error:
            raise ValueError(f"malformed osmium COPY record at line {line_number}: {error}") from error


def has_target_tag(tags: Mapping[str, str]) -> bool:
    return any(key in tags for key in TARGET_TAG_KEYS)
