"""Tests for deterministic geographic-density cache persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from osm_polygon_image_tag.artifacts.geography.cache import (
    CACHE_SCHEMA_VERSION,
    PerShardCache,
    _parse_cell_entry,
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
