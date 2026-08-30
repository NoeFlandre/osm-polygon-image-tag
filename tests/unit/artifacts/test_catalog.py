"""Catalog indexes keep metadata migration proportional to rows, not shards."""

import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import osm_polygon_image_tag.artifacts.asset_catalog as asset_catalog_module
import osm_polygon_image_tag.artifacts.catalog as catalog_module
from osm_polygon_image_tag.artifacts.asset_catalog import (
    _observation_values,
    asset_catalog_columns,
    sync_asset_catalog,
)
from osm_polygon_image_tag.artifacts.catalog import sync_catalog
from osm_polygon_image_tag.assets.manifest import (
    ASSET_MANIFEST_SCHEMA_VERSION,
    AssetManifest,
    AssetRunCounts,
    AssetSourceIdentity,
    ResolutionSnapshotIdentity,
)
from osm_polygon_image_tag.assets.schema import (
    ASSET_SCHEMA_VERSION,
    RESOLVER_CONTRACT_VERSION,
)
from osm_polygon_image_tag.core.manifest import Manifest, OutputIdentity, file_sha256


def test_catalog_indexes_shard_cleanup_columns(tmp_path: Path) -> None:
    catalog = sync_catalog(tmp_path, manifests=[])
    sync_asset_catalog(catalog, [])

    with sqlite3.connect(catalog) as connection:
        polygon_indexes = {row[1] for row in connection.execute("PRAGMA index_list(observations)")}
        asset_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(asset_observations)")
        }

    assert "observations_shard_idx" in polygon_indexes
    assert "asset_observations_shard_idx" in asset_indexes
    assert "asset_observations_duplicate_idx" in asset_indexes


def test_catalog_path_can_be_isolated_for_public_views(tmp_path: Path) -> None:
    custom = tmp_path / "catalog" / "public.sqlite"

    assert sync_catalog(tmp_path, manifests=[], catalog_path=custom) == custom
    assert custom.is_file()


def test_polygon_catalog_uses_default_batch_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, output = _asset_manifest(tmp_path, [_asset_row(1)], "region")
    seen: list[int] = []

    def capture_sync(
        _connection: sqlite3.Connection,
        _selected: Sequence[tuple[object, Path]],
        *,
        existing: dict[str, str],
        batch_size: int,
        emit: object,
    ) -> tuple[int, int, int]:
        del existing, emit
        seen.append(batch_size)
        return 0, 0, 0

    monkeypatch.setattr(catalog_module, "_sync_catalog_shards", capture_sync)
    sync_catalog(tmp_path, manifests=[(cast(Manifest, manifest), output)])

    assert seen == [8192]


def test_catalog_projection_and_row_conversion_are_stable() -> None:
    row = _asset_row(
        7,
        expires=datetime(2025, 1, 2, 3, 4, tzinfo=UTC),
        retry_after=datetime(2025, 1, 3, tzinfo=UTC),
    )

    assert asset_catalog_columns() == [
        "provider",
        "status",
        "canonical_reference",
        "provider_asset_id",
        "image_url",
        "page_url",
        "image_url_expires_at",
        "license_id",
        "category_truncated",
        "retry_after",
        "resolver_contract_version",
    ]
    assert _observation_values("assets/region.parquet", row) == (
        "assets/region.parquet",
        "image",
        "resolved",
        "https://example.test/7",
        "asset-7",
        "https://example.test/7.jpg",
        "https://example.test/page/7",
        "2025-01-02T03:04:00+00:00",
        "CC-BY",
        0,
        "2025-01-03T00:00:00+00:00",
        1,
    )


def _asset_manifest(
    tmp_path: Path, rows: list[dict[str, object]], name: str
) -> tuple[AssetManifest, Path]:
    output = tmp_path / "assets" / f"{name}.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({key: [row[key] for row in rows] for key in rows[0]}), output)
    manifest = AssetManifest(
        ASSET_MANIFEST_SCHEMA_VERSION,
        ASSET_SCHEMA_VERSION,
        RESOLVER_CONTRACT_VERSION,
        AssetSourceIdentity("data/region.parquet", 10, "a" * 64, len(rows)),
        ResolutionSnapshotIdentity(0, "b" * 64),
        OutputIdentity(
            f"assets/{name}.parquet", output.stat().st_size, file_sha256(output), len(rows)
        ),
        AssetRunCounts(len(rows), {"resolved": len(rows)}, {"image": len(rows)}, 0, 0, len(rows)),
    )
    return manifest, output


def _asset_row(
    index: int,
    *,
    expires: datetime | None = None,
    retry_after: datetime | None = None,
) -> dict[str, object]:
    return {
        "provider": "image",
        "status": "resolved",
        "canonical_reference": f"https://example.test/{index}",
        "provider_asset_id": f"asset-{index}",
        "image_url": f"https://example.test/{index}.jpg",
        "page_url": f"https://example.test/page/{index}",
        "image_url_expires_at": expires,
        "license_id": "CC-BY",
        "category_truncated": False,
        "retry_after": retry_after,
        "resolver_contract_version": 1,
    }


def test_catalog_indexes_rows_and_emits_counts(tmp_path: Path) -> None:
    manifest, output = _asset_manifest(
        tmp_path,
        [_asset_row(1, expires=datetime(2025, 1, 2, 3, 4, tzinfo=UTC)), _asset_row(2)],
        "region",
    )
    events: list[dict[str, object]] = []

    sync_asset_catalog(
        tmp_path / "catalog.sqlite",
        [(manifest, output)],
        batch_size=1,
        progress=events.append,
    )

    with sqlite3.connect(tmp_path / "catalog.sqlite") as connection:
        rows = connection.execute(
            "SELECT shard, provider, canonical_reference, expires_at "
            "FROM asset_observations ORDER BY canonical_reference"
        ).fetchall()
        shard = connection.execute("SELECT path, sha256 FROM asset_shards").fetchone()
    assert rows == [
        (
            "assets/region.parquet",
            "image",
            "https://example.test/1",
            "2025-01-02T03:04:00+00:00",
        ),
        ("assets/region.parquet", "image", "https://example.test/2", None),
    ]
    assert shard == (manifest.output.relative_path, manifest.output.sha256)
    assert events == [
        {"event": "metadata_asset_catalog_sync_started", "active_shards": 1},
        {
            "event": "metadata_asset_catalog_sync_completed",
            "active_shards": 1,
            "reused_shards": 0,
            "indexed_shards": 1,
            "indexed_rows": 2,
        },
    ]


def test_catalog_uses_bounded_column_projection_and_default_batch_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, output = _asset_manifest(
        tmp_path,
        [_asset_row(1, expires=datetime(2025, 1, 2, 3, 4, tzinfo=UTC))],
        "region",
    )
    calls: list[dict[str, object]] = []
    real_parquet_file = asset_catalog_module.pq.ParquetFile

    class SpyParquetFile:
        def __init__(self, path: Path) -> None:
            self._file = real_parquet_file(path)

        def iter_batches(self, **kwargs: object):
            calls.append(kwargs)
            return self._file.iter_batches(**kwargs)

    monkeypatch.setattr(asset_catalog_module.pq, "ParquetFile", SpyParquetFile)
    sync_asset_catalog(tmp_path / "catalog.sqlite", [(manifest, output)])

    assert calls == [
        {
            "batch_size": 8192,
            "columns": [
                "provider",
                "status",
                "canonical_reference",
                "provider_asset_id",
                "image_url",
                "page_url",
                "image_url_expires_at",
                "license_id",
                "category_truncated",
                "retry_after",
                "resolver_contract_version",
            ],
        }
    ]


def test_catalog_reuses_unchanged_shard_without_reading_parquet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, output = _asset_manifest(tmp_path, [_asset_row(1)], "region")
    catalog = tmp_path / "catalog.sqlite"
    sync_asset_catalog(catalog, [(manifest, output)])

    def fail_parquet(_path: Path) -> None:
        raise AssertionError("unchanged shard should be reused")

    monkeypatch.setattr(asset_catalog_module.pq, "ParquetFile", fail_parquet)
    events: list[dict[str, object]] = []
    sync_asset_catalog(catalog, [(manifest, output)], progress=events.append)

    assert events[-1]["reused_shards"] == 1
    assert events[-1]["indexed_shards"] == 0
    assert events[-1]["indexed_rows"] == 0


def test_catalog_continues_after_reused_shard_and_counts_retry_timestamp(tmp_path: Path) -> None:
    first, first_output = _asset_manifest(tmp_path, [_asset_row(1)], "first")
    second, second_output = _asset_manifest(
        tmp_path,
        [
            _asset_row(
                2,
                expires=datetime(2025, 1, 2, tzinfo=UTC),
                retry_after=datetime(2025, 1, 3, tzinfo=UTC),
            )
        ],
        "second",
    )
    third, third_output = _asset_manifest(tmp_path, [_asset_row(3)], "third")
    fourth, fourth_output = _asset_manifest(tmp_path, [_asset_row(4)], "fourth")
    catalog = tmp_path / "catalog.sqlite"
    sync_asset_catalog(catalog, [(first, first_output), (third, third_output)])
    events: list[dict[str, object]] = []

    sync_asset_catalog(
        catalog,
        [
            (first, first_output),
            (third, third_output),
            (second, second_output),
            (fourth, fourth_output),
        ],
        progress=events.append,
    )

    assert events[-1]["reused_shards"] == 2
    assert events[-1]["indexed_shards"] == 2
    assert events[-1]["indexed_rows"] == 2
    with sqlite3.connect(catalog) as connection:
        assert connection.execute(
            "SELECT retry_after FROM asset_observations WHERE shard = ?",
            ("assets/second.parquet",),
        ).fetchone() == ("2025-01-03T00:00:00+00:00",)


def test_catalog_removes_stale_shards(tmp_path: Path) -> None:
    first, first_output = _asset_manifest(tmp_path, [_asset_row(1)], "first")
    second, second_output = _asset_manifest(tmp_path, [_asset_row(2)], "second")
    catalog = tmp_path / "catalog.sqlite"
    sync_asset_catalog(catalog, [(first, first_output), (second, second_output)])

    sync_asset_catalog(catalog, [(second, second_output)])

    with sqlite3.connect(catalog) as connection:
        assert connection.execute("SELECT DISTINCT shard FROM asset_observations").fetchall() == [
            ("assets/second.parquet",)
        ]
        assert connection.execute("SELECT path FROM asset_shards").fetchall() == [
            ("assets/second.parquet",)
        ]


def test_catalog_rejects_row_count_mismatch_and_keeps_previous_state(tmp_path: Path) -> None:
    manifest, output = _asset_manifest(tmp_path, [_asset_row(1)], "region")
    catalog = tmp_path / "catalog.sqlite"
    sync_asset_catalog(catalog, [(manifest, output)])
    broken = replace(
        manifest,
        output=replace(
            manifest.output,
            sha256="d" * 64,
            row_count=manifest.output.row_count + 1,
        ),
    )

    with pytest.raises(ValueError, match="row mismatch"):
        sync_asset_catalog(catalog, [(broken, output)])

    with sqlite3.connect(catalog) as connection:
        assert connection.execute("SELECT COUNT(*) FROM asset_observations").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM asset_shards").fetchone() == (1,)
