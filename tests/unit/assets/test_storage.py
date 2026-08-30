from collections.abc import Iterator
from pathlib import Path

import pyarrow.parquet as pq
import pytest

import osm_polygon_image_tag.assets.storage as storage_module
from osm_polygon_image_tag.assets.storage import (
    AssetStorageError,
    AssetWriteResult,
    AtomicAssetWriter,
    validate_asset_parquet,
    write_asset_parquet,
)


def test_atomic_asset_writer_uses_default_batch_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pq, "ParquetWriter", lambda *_args, **_kwargs: object())

    writer = AtomicAssetWriter(tmp_path / "asset.parquet")
    try:
        assert writer._batch_size == 4096
    finally:
        writer._temporary_file.close()


def test_write_asset_parquet_uses_default_batch_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    class FakeWriter:
        result = AssetWriteResult(row_count=0, size_bytes=0)

        def __init__(self, _path: Path, *, batch_size: int) -> None:
            seen.append(batch_size)

        def __enter__(self) -> "FakeWriter":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def write(self, _rows: object) -> None:
            return None

    monkeypatch.setattr(storage_module, "AtomicAssetWriter", FakeWriter)

    result = storage_module.write_asset_parquet([], tmp_path / "asset.parquet")

    assert seen == [4096]
    assert result == FakeWriter.result


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


def test_asset_storage_cleans_temporary_file_when_writer_creation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "assets" / "region.assets.parquet"

    def fail_writer(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("writer creation failed")

    monkeypatch.setattr(pq, "ParquetWriter", fail_writer)

    with pytest.raises(RuntimeError, match="writer creation failed"):
        write_asset_parquet([asset_row(1)], path)

    assert not path.exists()
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
