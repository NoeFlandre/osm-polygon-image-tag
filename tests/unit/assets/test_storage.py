from collections.abc import Iterator
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from osm_polygon_image_tag.assets.storage import (
    AssetStorageError,
    validate_asset_parquet,
    write_asset_parquet,
)


def asset_row(index: int) -> dict[str, object]:
    return {
        "source_pbf": "region.osm.pbf",
        "source_polygon_shard": "data/region.parquet",
        "osm_type": "way",
        "osm_id": index,
        "osm_version": 1,
        "provider": "panoramax",
        "source_tag_key": "panoramax",
        "source_tag_value": f"id-{index}",
        "canonical_reference": f"id-{index}",
        "provider_asset_id": f"id-{index}",
        "asset_index": 0,
        "relation_kind": "direct_reference",
        "page_url": f"https://viewer.test/{index}",
        "image_url": f"https://cdn.test/{index}.jpg",
        "thumbnail_url": None,
        "image_url_expires_at": None,
        "mime_type": "image/jpeg",
        "width": 100,
        "height": 50,
        "license_id": None,
        "license_url": None,
        "author": None,
        "status": "resolved",
        "reason": None,
        "category_truncated": False,
        "retry_after": None,
        "resolver_contract_version": 1,
        "response_sha256": "a" * 64,
    }


def test_asset_storage_writes_bounded_zstd_batches_and_exact_schema(tmp_path: Path) -> None:
    path = tmp_path / "assets" / "region.assets.parquet"

    result = write_asset_parquet(
        (asset_row(index) for index in range(3)),
        path,
        batch_size=2,
    )

    assert result.row_count == 3
    assert result.size_bytes == path.stat().st_size
    parquet = pq.ParquetFile(path)
    assert parquet.metadata.num_row_groups == 2
    assert parquet.metadata.row_group(0).column(0).compression == "ZSTD"
    assert b"geo" not in (parquet.schema_arrow.metadata or {})
    validate_asset_parquet(path, expected_rows=3)


def test_asset_storage_failure_preserves_existing_finalized_asset(tmp_path: Path) -> None:
    path = tmp_path / "assets" / "region.assets.parquet"
    write_asset_parquet([asset_row(1)], path)
    before = path.read_bytes()

    def failing_rows() -> Iterator[dict[str, object]]:
        yield asset_row(2)
        raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        write_asset_parquet(failing_rows(), path, batch_size=1)

    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp"))


def test_asset_storage_rejects_symlinked_final_path(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"keep")
    path = tmp_path / "asset.parquet"
    path.symlink_to(target)

    with pytest.raises(AssetStorageError, match="symlink"):
        write_asset_parquet([asset_row(1)], path)

    assert target.read_bytes() == b"keep"


def test_asset_validation_checks_expected_row_count(tmp_path: Path) -> None:
    path = tmp_path / "asset.parquet"
    write_asset_parquet([asset_row(1)], path)

    with pytest.raises(AssetStorageError, match="row count"):
        validate_asset_parquet(path, expected_rows=2)
