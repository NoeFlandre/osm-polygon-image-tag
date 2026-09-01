"""Tests for deterministic geographic-density cache persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from osm_polygon_image_tag.artifacts.geography.cache import (
    CACHE_SCHEMA_VERSION,
    PerShardCache,
    _parse_cell_entry,
    _parse_shards,
    input_digest,
    load_shard_cache,
    load_stats_cache,
    write_shard_cache,
    write_stats_cache,
)
from osm_polygon_image_tag.artifacts.geography.h3 import DEFAULT_H3_RESOLUTION


def _shards() -> PerShardCache:
    return {
        "data/b.parquet": {
            "sha256": "b" * 64,
            "row_count": 3,
            "cells": {"8928308280fffff": 2, "8928308280bffff": 1},
        },
        "data/a.parquet": {
            "sha256": "a" * 64,
            "row_count": 1,
            "cells": {"89283082807ffff": 1},
        },
    }


def _shard_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "h3_resolution": DEFAULT_H3_RESOLUTION,
        "shards": {},
    }
    payload.update(overrides)
    return payload


def _write_json(cache_root: Path, filename: str, payload: object) -> None:
    cache_root.mkdir(exist_ok=True)
    (cache_root / filename).write_text(json.dumps(payload), encoding="utf-8")


def test_cache_digest_is_order_independent() -> None:
    first = [("data/b.parquet", "b" * 64, 3), ("data/a.parquet", "a" * 64, 1)]
    second = list(reversed(first))

    assert input_digest(first) == input_digest(second)
    assert len(input_digest(first)) == 64


def test_shard_cache_round_trip_is_sorted_and_versioned(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    write_shard_cache(cache_root, _shards())

    payload = json.loads((cache_root / "shards.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == CACHE_SCHEMA_VERSION
    assert payload["h3_resolution"] == DEFAULT_H3_RESOLUTION
    assert list(payload["shards"]) == ["data/a.parquet", "data/b.parquet"]
    assert [entry["h3_cell"] for entry in payload["shards"]["data/b.parquet"]["cells"]] == [
        "8928308280bffff",
        "8928308280fffff",
    ]
    assert load_shard_cache(cache_root) == _shards()


def test_shard_cache_rejects_malformed_payload(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    (cache_root / "shards.json").write_text("not-json", encoding="utf-8")

    assert load_shard_cache(cache_root) is None


def test_shard_cache_rejects_empty_shard_path(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "h3_resolution": DEFAULT_H3_RESOLUTION,
        "shards": {
            "": {
                "sha256": "a" * 64,
                "row_count": 1,
                "cells": [{"h3_cell": "89283082807ffff", "polygon_count": 1}],
            }
        },
    }
    (cache_root / "shards.json").write_text(json.dumps(payload), encoding="utf-8")

    assert load_shard_cache(cache_root) is None


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ([], "non-object root"),
        (_shard_payload(schema_version=CACHE_SCHEMA_VERSION - 1), "schema version"),
        (_shard_payload(h3_resolution=DEFAULT_H3_RESOLUTION + 1), "H3 resolution"),
        (_shard_payload(shards=[]), "non-object shards"),
        (
            _shard_payload(
                shards={"data/a.parquet": []},
            ),
            "non-object shard entry",
        ),
        (
            _shard_payload(
                shards={"data/a.parquet": {"sha256": "invalid", "row_count": 1, "cells": []}},
            ),
            "invalid SHA-256",
        ),
        (
            _shard_payload(
                shards={"data/a.parquet": {"sha256": "a" * 64, "row_count": True, "cells": []}},
            ),
            "boolean row count",
        ),
        (
            _shard_payload(
                shards={"data/a.parquet": {"sha256": "a" * 64, "row_count": 1, "cells": "bad"}},
            ),
            "non-list cells",
        ),
        (
            _shard_payload(
                shards={
                    "data/a.parquet": {
                        "sha256": "a" * 64,
                        "row_count": 1,
                        "cells": [{"h3_cell": "cell", "polygon_count": -1}],
                    }
                },
            ),
            "malformed cell entry",
        ),
        (
            _shard_payload(
                shards={
                    "data/a.parquet": {
                        "sha256": "a" * 64,
                        "row_count": 2,
                        "cells": [
                            {"h3_cell": "cell", "polygon_count": 1},
                            {"h3_cell": "cell", "polygon_count": 1},
                        ],
                    }
                },
            ),
            "duplicate cell",
        ),
        (
            _shard_payload(
                shards={
                    "data/a.parquet": {
                        "sha256": "a" * 64,
                        "row_count": 2,
                        "cells": [{"h3_cell": "cell", "polygon_count": 1}],
                    }
                },
            ),
            "mismatched cell total",
        ),
    ],
)
def test_shard_cache_rejects_invalid_payloads(tmp_path: Path, payload: object, reason: str) -> None:
    _write_json(tmp_path, "shards.json", payload)

    assert load_shard_cache(tmp_path) is None, reason


def test_shard_cache_rejects_non_string_shard_key() -> None:
    assert _parse_shards({1: {}}) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"h3_cell": "cell", "polygon_count": 2}, ("cell", 2)),
        (None, None),
        ({"h3_cell": "", "polygon_count": 1}, None),
        ({"h3_cell": "cell", "polygon_count": -1}, None),
        ({"h3_cell": "cell", "polygon_count": True}, None),
    ],
)
def test_cache_cell_entry_parser_rejects_invalid_shapes(
    value: object, expected: tuple[str, int] | None
) -> None:
    assert _parse_cell_entry(value) == expected


def test_stats_cache_round_trip_and_schema_validation(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    write_stats_cache(
        cache_root,
        input_digest="a" * 64,
        input_shard_count=2,
        h3_resolution=DEFAULT_H3_RESOLUTION,
        cell_count=4,
        polygon_rows=5,
        min_cell_count=1,
        max_cell_count=2,
    )

    loaded = load_stats_cache(cache_root)
    assert loaded == {
        "schema_version": CACHE_SCHEMA_VERSION,
        "input_digest": "a" * 64,
        "input_shard_count": 2,
        "h3_resolution": DEFAULT_H3_RESOLUTION,
        "cell_count": 4,
        "polygon_rows": 5,
        "min_cell_count": 1,
        "max_cell_count": 2,
    }

    path = cache_root / "pipeline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = CACHE_SCHEMA_VERSION - 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_stats_cache(cache_root) is None


def test_cache_loaders_return_none_for_missing_files(tmp_path: Path) -> None:
    assert load_shard_cache(tmp_path) is None
    assert load_stats_cache(tmp_path) is None


def test_shard_cache_returns_none_when_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_json(tmp_path, "shards.json", _shard_payload())

    def fail_read_text(*_args: object, **_kwargs: object) -> str:
        raise OSError("cache unavailable")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    assert load_shard_cache(tmp_path) is None


def test_stats_cache_returns_none_when_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_json(tmp_path, "pipeline.json", {})

    def fail_read_text(*_args: object, **_kwargs: object) -> str:
        raise OSError("cache unavailable")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    assert load_stats_cache(tmp_path) is None


def test_stats_cache_rejects_malformed_json(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    (cache_root / "pipeline.json").write_text("not-json", encoding="utf-8")

    assert load_stats_cache(cache_root) is None


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"schema_version": CACHE_SCHEMA_VERSION - 1, "h3_resolution": DEFAULT_H3_RESOLUTION},
        {"schema_version": CACHE_SCHEMA_VERSION, "h3_resolution": DEFAULT_H3_RESOLUTION + 1},
    ],
)
def test_stats_cache_rejects_invalid_payloads(tmp_path: Path, payload: object) -> None:
    _write_json(tmp_path, "pipeline.json", payload)

    assert load_stats_cache(tmp_path) is None
