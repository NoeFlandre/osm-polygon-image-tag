"""Deterministic JSON serialization shared across pipeline layers."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import cast

__all__ = ["canonical_json", "canonical_json_bytes"]


def _jsonable(value: object) -> object:
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return _jsonable_mapping(cast(dict[object, object], value))
    if isinstance(value, list):
        return _jsonable_list(value)
    return value


def _jsonable_mapping(value: dict[object, object]) -> dict[str, object]:
    return {
        str(key): _jsonable(item)
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }


def _jsonable_list(value: Sequence[object]) -> list[object]:
    return [_jsonable(item) for item in value]


def canonical_json(value: object) -> str:
    """Return deterministic JSON for pipeline identity and metadata payloads."""
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_json_bytes(value: object, *, newline: bool = False) -> bytes:
    """Return UTF-8 encoded canonical JSON, optionally terminated by a newline."""
    payload = canonical_json(value).encode("utf-8")
    return payload + (b"\n" if newline else b"")
