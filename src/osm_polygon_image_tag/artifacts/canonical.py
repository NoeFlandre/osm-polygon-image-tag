"""Canonical serialization helpers for deterministic artifact identities."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import cast

__all__ = ["canonical_json"]


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
    """Return deterministic JSON for artifact identity payloads."""
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
