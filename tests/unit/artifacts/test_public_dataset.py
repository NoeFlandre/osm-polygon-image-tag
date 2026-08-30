from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely import to_wkb
from shapely.geometry import Polygon

import osm_polygon_image_tag.artifacts.public_asset_checkpoint as checkpoint_module
import osm_polygon_image_tag.artifacts.public_assets as public_assets_module
import osm_polygon_image_tag.artifacts.public_dataset as public_dataset_module
from osm_polygon_image_tag.artifacts.asset_inventory import verified_asset_manifests
from osm_polygon_image_tag.artifacts.asset_statistics import public_asset_statistics
from osm_polygon_image_tag.artifacts.public_dataset import (
    LEGACY_PUBLIC_ASSET_RELATIVE,
    PUBLIC_IMAGE_RELATIVE,
    PUBLIC_LINK_RELATIVE,
    _public_polygon_schema_matches,
    _resolved_polygon_row_count,
    build_public_dataset,
    public_polygon_schema,
)
from osm_polygon_image_tag.artifacts.public_polygon_accumulator import (
    _advance_polygon_source_group,
    _PolygonAccumulator,
    _remove_incompatible_polygon_checkpoint,
)
from osm_polygon_image_tag.artifacts.storage import write_geoparquet
from osm_polygon_image_tag.assets.manifest import (
    ASSET_MANIFEST_SCHEMA_VERSION,
    AssetManifest,
    AssetRunCounts,
    AssetSourceIdentity,
    ResolutionSnapshotIdentity,
    write_asset_manifest,
)
from osm_polygon_image_tag.assets.schema import ASSET_SCHEMA_VERSION, RESOLVER_CONTRACT_VERSION
from osm_polygon_image_tag.assets.storage import write_asset_parquet
from osm_polygon_image_tag.core.manifest import (
    DATASET_SCHEMA_VERSION,
    PROCESSING_CONTRACT_VERSION,
    Manifest,
    OutputIdentity,
    RunCounts,
    SourceIdentity,
    file_sha256,
    write_manifest,
)
from osm_polygon_image_tag.core.serialization import canonical_json


def _polygon_row(
    source: str,
    *,
    osm_id: int = 1,
    osm_version: int = 1,
    image: str = "https://example.test/a.jpg",
) -> dict[str, object]:
    polygon = Polygon([(4, 50), (4.01, 50), (4.01, 50.01), (4, 50.01)])
    return {
        "osm_type": "way",
        "osm_id": osm_id,
        "osm_version": osm_version,
        "osm_changeset": 1,
        "osm_timestamp": None,
        "source_pbf": source,
        "source_feature_id": f"{source}|way|{osm_id}|{osm_version}",
        "geometry": to_wkb(polygon),
        "geometry_type": "Polygon",
        "area_m2": 1.0,
        "bbox_min_lon": 4.0,
        "bbox_min_lat": 50.0,
        "bbox_max_lon": 4.01,
        "bbox_max_lat": 50.01,
        "tags": {"image": image, "name": "Example"},
        "image": image,
        "wikimedia_commons": None,
        "mapillary": None,
        "panoramax": None,
        "panoramax_values": {},
        "kartaview": None,
        "flickr": None,
        "bubbleid": None,
    }


def _asset_row(
    source: str,
    *,
    source_shard: str = "data/source.parquet",
    osm_id: int = 1,
    osm_version: int = 1,
    source_tag_key: str = "image",
    source_tag_value: str = "https://example.test/a.jpg",
    canonical_reference: str = "https://example.test/a.jpg",
    provider_asset_id: str = "a",
    relation_kind: str = "direct_reference",
    image_url: str | None = "https://example.test/a.jpg",
) -> dict[str, object]:
    return {
        "source_pbf": source,
        "source_polygon_shard": source_shard,
        "osm_type": "way",
        "osm_id": osm_id,
        "osm_version": osm_version,
        "provider": "image",
        "source_tag_key": source_tag_key,
        "source_tag_value": source_tag_value,
        "canonical_reference": canonical_reference,
        "provider_asset_id": provider_asset_id,
        "asset_index": 0,
        "relation_kind": relation_kind,
        "page_url": "https://example.test/a.jpg",
        "image_url": image_url,
        "thumbnail_url": None,
        "image_url_expires_at": None,
        "mime_type": "image/jpeg",
        "width": 100,
        "height": 100,
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


def _write_polygon_manifest(root: Path, source: str, rows: list[dict[str, object]]) -> None:
    output = root / "data" / f"{Path(source).stem}.parquet"
    write_geoparquet(rows, output)
    write_manifest(
        Manifest(
            1,
            PROCESSING_CONTRACT_VERSION,
            DATASET_SCHEMA_VERSION,
            SourceIdentity(source, 1, 1, "b" * 64),
            OutputIdentity(
                output.relative_to(root).as_posix(),
                output.stat().st_size,
                file_sha256(output),
                len(rows),
            ),
            "test",
            RunCounts(len(rows), {}),
        ),
        root / "manifests" / f"{Path(source).stem}.manifest.json",
    )


def _write_asset_manifest(
    root: Path, source: str, source_shard: str, rows: list[dict[str, object]]
) -> None:
    output = root / "assets" / f"{Path(source).stem}.assets.parquet"
    write = write_asset_parquet(rows, output)
    write_asset_manifest(
        AssetManifest(
            ASSET_MANIFEST_SCHEMA_VERSION,
            ASSET_SCHEMA_VERSION,
            RESOLVER_CONTRACT_VERSION,
            AssetSourceIdentity(source_shard, 1, "c" * 64, len(rows)),
            ResolutionSnapshotIdentity(1, "d" * 64),
            OutputIdentity(
                output.relative_to(root).as_posix(),
                write.size_bytes,
                file_sha256(output),
                write.row_count,
            ),
            AssetRunCounts(
                len(rows), {"resolved": len(rows)}, {"image": len(rows)}, 0, 0, len(rows)
            ),
        ),
        root / "asset-manifests" / f"{Path(source).stem}.assets.manifest.json",
    )


def test_public_dataset_deduplicates_identity_and_preserves_provenance(tmp_path: Path) -> None:
    _write_polygon_manifest(tmp_path, "z-region.osm.pbf", [_polygon_row("z-region.osm.pbf")])
    _write_polygon_manifest(tmp_path, "a-region.osm.pbf", [_polygon_row("a-region.osm.pbf")])
    _write_asset_manifest(
        tmp_path,
        "z-region.osm.pbf",
        "data/z-region.parquet",
        [_asset_row("z-region.osm.pbf", source_shard="data/z-region.parquet")],
    )
    _write_asset_manifest(
        tmp_path,
        "a-region.osm.pbf",
        "data/a-region.parquet",
        [_asset_row("a-region.osm.pbf", source_shard="data/a-region.parquet")],
    )

    result = build_public_dataset(tmp_path)
    assert result.polygon_rows == 1
    assert result.duplicate_polygon_rows == 1
    assert result.image_rows == 1
    assert result.link_rows == 1
    assert result.duplicate_image_rows == 1
    assert result.duplicate_link_rows == 1

    polygon = pq.read_table(result.polygon_path).to_pylist()[0]
    image = pq.read_table(result.image_path).to_pylist()[0]
    link = pq.read_table(result.link_path).to_pylist()[0]
    assert polygon["source_pbf"] == "a-region.osm.pbf"
    assert polygon["source_pbfs"] == ["a-region.osm.pbf", "z-region.osm.pbf"]
    assert image["source_pbfs"] == ["a-region.osm.pbf", "z-region.osm.pbf"]
    assert link["source_pbfs"] == ["a-region.osm.pbf", "z-region.osm.pbf"]

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["polygon_rows"] == 1
    assert manifest["duplicate_polygon_rows"] == 1
    assert manifest["image_rows"] == 1
    assert manifest["link_rows"] == 1

    public_assets_module.validate_public_image_parquet(result.image_path, expected_rows=1)
    public_assets_module.validate_public_link_parquet(result.link_path, expected_rows=1)
    with pytest.raises(ValueError, match="image row count"):
        public_assets_module.validate_public_image_parquet(result.image_path, expected_rows=2)
    with pytest.raises(ValueError, match="link row count"):
        public_assets_module.validate_public_link_parquet(result.link_path, expected_rows=2)


def test_public_dataset_keeps_fallback_identity_and_optional_asset_fields(tmp_path: Path) -> None:
    _write_polygon_manifest(tmp_path, "region.osm.pbf", [_polygon_row("region.osm.pbf")])
    row = _asset_row(
        "region.osm.pbf",
        source_tag_value="File:Example.jpg",
        canonical_reference="File:Example.jpg",
        provider_asset_id="example-id",
        relation_kind="category_membership",
        image_url=None,
    ) | {"status": "resolved_page_only", "category_truncated": True}
    _write_asset_manifest(
        tmp_path,
        "region.osm.pbf",
        "data/region.parquet",
        [row],
    )

    result = build_public_dataset(tmp_path)
    image = pq.read_table(result.image_path).to_pylist()[0]
    link = pq.read_table(result.link_path).to_pylist()[0]

    assert image["image_id"] == public_assets_module.image_id(row)
    assert image["image_url"] is None
    assert image["provider_asset_id"] == "example-id"
    assert image["category_truncated"] is True
    assert link["relation_kind"] == "category_membership"


def test_columnar_asset_view_exposes_the_same_fields_as_an_asset_row() -> None:
    row = _asset_row("region.osm.pbf")
    batch = public_assets_module._AssetBatch(
        {name: [row[name]] for name in public_assets_module._ASSET_DEDUP_COLUMNS},
        1,
    )
    columns = public_assets_module._AssetColumns.from_batch(batch)
    view = public_assets_module._ColumnarAssetRow(columns)
    view.index = 0

    assert dict(view) == {name: row[name] for name in public_assets_module._ASSET_DEDUP_COLUMNS}


def test_columnar_asset_batch_values_match_mapping_asset_batch_values() -> None:
    row = _asset_row("region.osm.pbf")
    polygon = _polygon_row("region.osm.pbf")
    batch = public_assets_module._AssetBatch(
        {name: [row[name]] for name in public_assets_module._ASSET_DEDUP_COLUMNS},
        1,
    )

    mapping_values = public_assets_module._prepare_batch_values([(row, polygon)])
    columnar_values = public_assets_module._prepare_columnar_batch_values(
        batch, {("way", 1): polygon}
    )

    assert columnar_values == mapping_values


def test_public_dataset_resumes_after_source_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_polygon_manifest(tmp_path, "a-region.osm.pbf", [_polygon_row("a-region.osm.pbf")])
    _write_polygon_manifest(
        tmp_path,
        "b-region.osm.pbf",
        [_polygon_row("b-region.osm.pbf", osm_id=2)],
    )

    def batches(output: Path):
        for batch in pq.ParquetFile(output).iter_batches(batch_size=8192):
            yield batch.to_pylist()

    calls: list[str] = []

    def interrupted(output: Path):
        calls.append(output.name)
        if len(calls) == 2:
            raise RuntimeError("stop after first source")
        yield from batches(output)

    monkeypatch.setattr(public_dataset_module, "_iter_source_batches", interrupted, raising=False)
    with pytest.raises(RuntimeError, match="stop after first source"):
        build_public_dataset(tmp_path)

    checkpoint = tmp_path / "tmp" / ".public-polygons.sqlite"
    assert checkpoint.is_file()

    calls.clear()

    def resumed(output: Path):
        calls.append(output.name)
        yield from batches(output)

    monkeypatch.setattr(public_dataset_module, "_iter_source_batches", resumed, raising=False)
    result = build_public_dataset(tmp_path)

    assert calls == ["b-region.osm.parquet"]
    assert result.polygon_rows == 2
    assert result.duplicate_polygon_rows == 0
    assert not checkpoint.exists()


def test_public_dataset_resumes_after_asset_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_polygon_manifest(tmp_path, "a-region.osm.pbf", [_polygon_row("a-region.osm.pbf")])
    _write_polygon_manifest(
        tmp_path,
        "b-region.osm.pbf",
        [_polygon_row("b-region.osm.pbf", osm_id=2)],
    )
    _write_asset_manifest(
        tmp_path,
        "a-region.osm.pbf",
        "data/a-region.parquet",
        [_asset_row("a-region.osm.pbf")],
    )
    _write_asset_manifest(
        tmp_path,
        "b-region.osm.pbf",
        "data/b-region.parquet",
        [_asset_row("b-region.osm.pbf", osm_id=2)],
    )

    original = public_assets_module._iter_batches
    calls: list[str] = []

    def interrupted(output: Path):
        calls.append(output.name)
        if len(calls) == 2:
            raise RuntimeError("stop after first asset source")
        yield from original(output)

    monkeypatch.setattr(public_assets_module, "_iter_batches", interrupted, raising=False)
    with pytest.raises(RuntimeError, match="stop after first asset source"):
        build_public_dataset(tmp_path)

    checkpoint = tmp_path / "tmp" / ".public-assets.sqlite"
    assert checkpoint.is_file()

    calls.clear()
    monkeypatch.setattr(
        public_dataset_module,
        "_write_polygon_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("completed polygon output should be reused")
        ),
    )

    def resumed(output: Path):
        calls.append(output.name)
        yield from original(output)

    monkeypatch.setattr(public_assets_module, "_iter_batches", resumed, raising=False)
    result = build_public_dataset(tmp_path)

    assert calls == ["b-region.osm.assets.parquet"]
    assert result.image_rows == 1
    assert result.link_rows == 2
    assert not checkpoint.exists()


def test_public_dataset_resumes_with_external_asset_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        checkpoint_module.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"free": 20 * 1024**3})(),
    )
    _write_polygon_manifest(tmp_path, "a-region.osm.pbf", [_polygon_row("a-region.osm.pbf")])
    _write_polygon_manifest(
        tmp_path,
        "b-region.osm.pbf",
        [_polygon_row("b-region.osm.pbf", osm_id=2)],
    )
    _write_asset_manifest(
        tmp_path,
        "a-region.osm.pbf",
        "data/a-region.parquet",
        [_asset_row("a-region.osm.pbf")],
    )
    _write_asset_manifest(
        tmp_path,
        "b-region.osm.pbf",
        "data/b-region.parquet",
        [_asset_row("b-region.osm.pbf", osm_id=2)],
    )

    scratch = tmp_path.parent / f"{tmp_path.name}-local-checkpoint"
    original = public_assets_module._iter_batches
    calls: list[str] = []

    def interrupted(output: Path):
        calls.append(output.name)
        if len(calls) == 2:
            raise RuntimeError("stop after first external source")
        yield from original(output)

    monkeypatch.setattr(public_assets_module, "_iter_batches", interrupted, raising=False)
    with pytest.raises(RuntimeError, match="stop after first external source"):
        build_public_dataset(tmp_path, asset_checkpoint_root=scratch)

    checkpoint = scratch / ".public-assets.sqlite"
    assert checkpoint.is_file()
    assert not (tmp_path / "tmp" / ".public-assets.sqlite").exists()

    calls.clear()
    monkeypatch.setattr(
        public_dataset_module,
        "_write_polygon_rows",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("completed polygon output should be reused")
        ),
    )

    def resumed(output: Path):
        calls.append(output.name)
        yield from original(output)

    monkeypatch.setattr(public_assets_module, "_iter_batches", resumed, raising=False)
    result = build_public_dataset(tmp_path, asset_checkpoint_root=scratch)

    assert calls == ["b-region.osm.assets.parquet"]
    assert result.image_rows == 1
    assert result.link_rows == 2
    assert not checkpoint.exists()


def test_external_asset_checkpoint_is_seeded_from_durable_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        checkpoint_module.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"free": 20 * 1024**3})(),
    )
    _write_polygon_manifest(tmp_path, "a-region.osm.pbf", [_polygon_row("a-region.osm.pbf")])
    _write_polygon_manifest(
        tmp_path,
        "b-region.osm.pbf",
        [_polygon_row("b-region.osm.pbf", osm_id=2)],
    )
    _write_asset_manifest(
        tmp_path,
        "a-region.osm.pbf",
        "data/a-region.parquet",
        [_asset_row("a-region.osm.pbf")],
    )
    _write_asset_manifest(
        tmp_path,
        "b-region.osm.pbf",
        "data/b-region.parquet",
        [_asset_row("b-region.osm.pbf", osm_id=2)],
    )

    original = public_assets_module._iter_batches
    calls: list[str] = []

    def interrupted(output: Path):
        calls.append(output.name)
        if len(calls) == 2:
            raise RuntimeError("stop after durable source")
        yield from original(output)

    monkeypatch.setattr(public_assets_module, "_iter_batches", interrupted, raising=False)
    with pytest.raises(RuntimeError, match="stop after durable source"):
        build_public_dataset(tmp_path)

    durable = tmp_path / "tmp" / ".public-assets.sqlite"
    scratch = tmp_path.parent / f"{tmp_path.name}-local-checkpoint"
    assert durable.is_file()

    calls.clear()

    def resumed(output: Path):
        calls.append(output.name)
        yield from original(output)

    monkeypatch.setattr(public_assets_module, "_iter_batches", resumed, raising=False)
    result = build_public_dataset(tmp_path, asset_checkpoint_root=scratch)

    assert calls == ["b-region.osm.assets.parquet"]
    assert result.image_rows == 1
    assert not durable.exists()
    assert not (scratch / ".public-assets.sqlite").exists()


def test_polygon_accumulator_keeps_latest_row_and_sources_on_disk(tmp_path: Path) -> None:
    accumulator = _PolygonAccumulator(tmp_path / "polygons.sqlite")
    try:
        accumulator.add_many(
            [
                _polygon_row("old-region.osm.pbf", osm_version=1),
                _polygon_row("new-region.osm.pbf", osm_version=2),
            ]
        )

        rows = list(accumulator.rows())

        assert accumulator.input_rows == 2
        assert len(rows) == 1
        assert rows[0]["osm_version"] == 2
        assert rows[0]["source_pbf"] == "new-region.osm.pbf"
        assert rows[0]["source_pbfs"] == [
            "new-region.osm.pbf",
            "old-region.osm.pbf",
        ]
    finally:
        accumulator.close()


def test_polygon_checkpoint_records_output_row_count(tmp_path: Path) -> None:
    accumulator = _PolygonAccumulator(tmp_path / "polygons.sqlite")
    try:
        accumulator.record_public_output("a" * 64, 42)
        assert accumulator.public_output_sha256() == "a" * 64
        assert accumulator.public_output_rows() == 42
    finally:
        accumulator.close()


def test_polygon_checkpoint_row_count_rejects_missing_invalid_and_negative_values(
    tmp_path: Path,
) -> None:
    accumulator = _PolygonAccumulator(tmp_path / "polygons.sqlite")
    try:
        assert accumulator.public_output_rows() is None
        accumulator.connection.execute(
            "INSERT INTO checkpoint_metadata(key, value) VALUES (?, ?)",
            ("public_output_rows", "not-an-integer"),
        )
        accumulator.connection.commit()
        assert accumulator.public_output_rows() is None
        accumulator.connection.execute(
            "UPDATE checkpoint_metadata SET value = ? WHERE key = ?",
            ("-1", "public_output_rows"),
        )
        accumulator.connection.commit()
        assert accumulator.public_output_rows() is None
    finally:
        accumulator.close()


def test_polygon_source_group_advances_and_rejects_missing_identity() -> None:
    groups = iter([("way", 1, "a"), ("way", 2, "b")])

    assert _advance_polygon_source_group(next(groups), groups, ("way", 2)) == ("way", 2, "b")
    with pytest.raises(ValueError, match="provenance is incomplete"):
        _advance_polygon_source_group(None, iter(()), ("way", 3))


def test_public_polygon_schema_comparison_checks_metadata_and_fields() -> None:
    expected = public_polygon_schema()

    assert _public_polygon_schema_matches(expected, expected)
    assert not _public_polygon_schema_matches(expected, expected.with_metadata({b"other": b"1"}))


def test_incompatible_polygon_checkpoint_is_removed_only_when_inputs_are_known(
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "polygons.sqlite"
    checkpoint.write_bytes(b"not a sqlite database")
    _remove_incompatible_polygon_checkpoint(checkpoint, None)
    assert checkpoint.exists()

    _remove_incompatible_polygon_checkpoint(checkpoint, ["a" * 64])
    assert not checkpoint.exists()


def test_manifest_polygon_row_count_reuses_matching_output(tmp_path: Path) -> None:
    output = tmp_path / "public" / "polygons.parquet"
    output.parent.mkdir()
    output.write_bytes(b"public polygon output")
    digest = file_sha256(output)
    (tmp_path / "public" / "public-manifest.json").write_text(
        json.dumps(
            {
                "polygon_output": {
                    "sha256": digest,
                    "size_bytes": output.stat().st_size,
                    "row_count": 42,
                }
            }
        )
    )

    assert public_dataset_module._manifest_polygon_row_count(tmp_path, output, digest) == 42


def test_resolved_polygon_row_count_uses_checkpoint_manifest_then_file(
    tmp_path: Path,
) -> None:
    output = tmp_path / "public" / "polygons.parquet"
    output.parent.mkdir()
    pq.write_table(pa.table({"value": [1, 2]}), output)
    digest = file_sha256(output)
    assert _resolved_polygon_row_count(tmp_path, output, digest, 7) == 7

    manifest_path = tmp_path / "public" / "public-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "polygon_output": {
                    "sha256": digest,
                    "size_bytes": output.stat().st_size,
                    "row_count": 42,
                }
            }
        )
    )
    assert _resolved_polygon_row_count(tmp_path, output, digest, None) == 42

    manifest_path.unlink()
    assert _resolved_polygon_row_count(tmp_path, output, digest, None) == 2


def test_public_dataset_reuses_polygon_row_count_without_sqlite_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_polygon_manifest(tmp_path, "region.osm.pbf", [_polygon_row("region.osm.pbf")])
    _write_asset_manifest(
        tmp_path,
        "region.osm.pbf",
        "data/region.parquet",
        [_asset_row("region.osm.pbf")],
    )
    first = build_public_dataset(tmp_path)
    first.image_path.unlink()
    first.link_path.unlink()

    manifests = public_dataset_module.verified_manifests(tmp_path)
    checkpoint = tmp_path / "tmp" / ".public-polygons.sqlite"
    checkpoint.parent.mkdir()
    accumulator = _PolygonAccumulator(
        checkpoint, input_hashes=[manifest.output.sha256 for manifest, _ in manifests]
    )
    try:
        accumulator.complete_source(0, manifests[0][0].output.sha256, 1)
        accumulator.record_public_output(first.polygon_manifest.output.sha256, 1)
    finally:
        accumulator.close()

    monkeypatch.setattr(
        _PolygonAccumulator,
        "unique_count",
        lambda _self: (_ for _ in ()).throw(AssertionError("SQLite count scan was used")),
    )
    result = build_public_dataset(tmp_path)

    assert result.polygon_rows == 1
    assert result.image_rows == 1


def test_public_dataset_builds_asset_lookup_from_public_polygons(tmp_path: Path) -> None:
    assert not hasattr(_PolygonAccumulator, "canonical_index")
    _write_polygon_manifest(tmp_path, "region.osm.pbf", [_polygon_row("region.osm.pbf")])
    _write_asset_manifest(
        tmp_path,
        "region.osm.pbf",
        "data/region.parquet",
        [_asset_row("region.osm.pbf")],
    )

    result = build_public_dataset(tmp_path)

    assert result.image_rows == 1
    assert result.link_rows == 1


def test_asset_checkpoint_uses_bounded_page_cache(tmp_path: Path) -> None:
    accumulator = public_assets_module._Accumulator(
        tmp_path / "assets.sqlite", input_hashes=["synthetic"]
    )
    try:
        cache_size = accumulator.connection.execute("PRAGMA cache_size").fetchone()[0]
        assert cache_size == -public_assets_module.PUBLIC_ASSET_SQLITE_CACHE_KIB
    finally:
        accumulator.close()


def test_asset_checkpoint_uses_bounded_mmap_window(tmp_path: Path) -> None:
    accumulator = public_assets_module._Accumulator(
        tmp_path / "assets.sqlite", input_hashes=["synthetic"]
    )
    try:
        mmap_size = accumulator.connection.execute("PRAGMA mmap_size").fetchone()[0]
        assert mmap_size == public_assets_module.PUBLIC_ASSET_SQLITE_MMAP_BYTES
    finally:
        accumulator.close()


def test_asset_checkpoint_uses_large_pages_for_new_database(tmp_path: Path) -> None:
    accumulator = public_assets_module._Accumulator(
        tmp_path / "assets.sqlite", input_hashes=["synthetic"]
    )
    try:
        page_size = accumulator.connection.execute("PRAGMA page_size").fetchone()[0]
        assert page_size == 65_536
    finally:
        accumulator.close()


def test_external_asset_checkpoint_has_a_conservative_storage_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gibibyte = 1024**3
    monkeypatch.setattr(
        checkpoint_module.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"free": 20 * gibibyte})(),
    )
    path = tmp_path / "assets.sqlite"

    limit = public_assets_module._checkpoint_max_bytes(path)

    assert limit == 6 * gibibyte


def test_external_asset_limit_allows_existing_checkpoint_growth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gibibyte = 1024**3
    monkeypatch.setattr(
        checkpoint_module.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"free": 20 * gibibyte})(),
    )
    path = tmp_path / "assets.sqlite"
    with path.open("wb") as handle:
        handle.truncate(5 * gibibyte)

    limit = public_assets_module._checkpoint_max_bytes(path)

    assert limit == public_assets_module.PUBLIC_ASSET_CHECKPOINT_MAX_FILE_BYTES


def test_asset_checkpoint_applies_storage_limit_to_sqlite(
    tmp_path: Path,
) -> None:
    limit = 128 * public_assets_module.PUBLIC_ASSET_SQLITE_PAGE_SIZE
    accumulator = public_assets_module._Accumulator(
        tmp_path / "assets.sqlite", input_hashes=["synthetic"], max_bytes=limit
    )
    try:
        max_page_count = accumulator.connection.execute("PRAGMA max_page_count").fetchone()[0]
        assert max_page_count == 128
    finally:
        accumulator.close()


def test_asset_checkpoint_preserves_page_size_when_resuming(tmp_path: Path) -> None:
    path = tmp_path / "assets.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA page_size=4096")
    connection.executescript(
        """
        CREATE TABLE checkpoint_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE checkpoint_sources (
            source_index INTEGER PRIMARY KEY,
            source_sha256 TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            orphan_count INTEGER NOT NULL
        );
        INSERT INTO checkpoint_metadata VALUES ('schema_version', '1');
        INSERT INTO checkpoint_metadata VALUES ('input_hashes', '[\"synthetic\"]');
        INSERT INTO checkpoint_metadata VALUES ('polygon_fingerprint', '');
        CREATE TABLE existing_checkpoint (value INTEGER);
        INSERT INTO existing_checkpoint VALUES (1);
        """
    )
    connection.commit()
    connection.close()

    accumulator = public_assets_module._Accumulator(path, input_hashes=["synthetic"])
    try:
        page_size = accumulator.connection.execute("PRAGMA page_size").fetchone()[0]
        assert page_size == 4096
    finally:
        accumulator.close()


def test_asset_dedup_reader_skips_unused_source_shard_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pyarrow.parquet as pq

    output = tmp_path / "assets.parquet"
    write_asset_parquet([_asset_row("region.osm.pbf")], output)
    original_iter_batches = pq.ParquetFile.iter_batches
    requested_columns: list[object] = []
    requested_batch_sizes: list[object] = []

    def capture_iter_batches(parquet: pq.ParquetFile, *args: object, **kwargs: object) -> object:
        requested_columns.append(kwargs.get("columns"))
        requested_batch_sizes.append(kwargs.get("batch_size"))
        return original_iter_batches(parquet, *args, **kwargs)

    monkeypatch.setattr(pq.ParquetFile, "iter_batches", capture_iter_batches)
    batches = list(public_assets_module._iter_batches(output))

    assert batches and batches[0].row_count == 1
    assert batches[0].columns["source_pbf"] == ["region.osm.pbf"]
    assert "source_polygon_shard" not in batches[0].columns
    assert requested_columns == [
        tuple(
            name
            for name in public_assets_module.asset_schema().names
            if name != "source_polygon_shard"
        )
    ]
    assert requested_batch_sizes == [8192]


def test_public_polygon_source_reader_uses_default_batch_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "source.parquet"
    pq.write_table(pa.table({"value": [1]}), output)
    original_iter_batches = public_dataset_module.pq.ParquetFile.iter_batches
    calls: list[dict[str, object]] = []

    def capture_iter_batches(parquet: pq.ParquetFile, *args: object, **kwargs: object) -> object:
        calls.append(kwargs)
        return original_iter_batches(parquet, *args, **kwargs)

    monkeypatch.setattr(public_dataset_module.pq.ParquetFile, "iter_batches", capture_iter_batches)

    assert list(public_dataset_module._iter_source_batches(output)) == [[{"value": 1}]]
    assert calls == [{"batch_size": 8192}]


def test_public_polygon_writer_uses_default_batch_size(tmp_path: Path) -> None:
    output = tmp_path / "public.parquet"
    row = {**_polygon_row("region.osm.pbf"), "source_pbfs": ["region.osm.pbf"]}
    tags = row["tags"]
    assert isinstance(tags, dict)
    row["tags"] = [{"key": key, "value": value} for key, value in tags.items()]
    row["panoramax_values"] = []

    count = public_dataset_module._write_polygon_rows(
        (row.copy() for _ in range(8192)),
        output,
    )

    parquet = pq.ParquetFile(output)
    assert count == 8192
    assert [parquet.metadata.row_group(index).num_rows for index in range(2)] == [4096, 4096]


def test_canonical_json_serializes_nested_binary_and_timestamps() -> None:
    assert (
        canonical_json({"bytes": b"\x01", "when": datetime(2024, 1, 2), "items": [b"\x02"]})
        == '{"bytes":{"__bytes__":"01"},"items":[{"__bytes__":"02"}],"when":"2024-01-02T00:00:00"}'
    )


def test_asset_accumulator_orders_index_writes_by_key(tmp_path: Path) -> None:
    accumulator = public_assets_module._Accumulator(tmp_path / "assets.sqlite")

    class RecordingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection
            self.calls: list[tuple[str, list[tuple[object, ...]]]] = []

        def executemany(self, sql: str, values: Iterable[tuple[object, ...]]) -> sqlite3.Cursor:
            materialized = list(values)
            self.calls.append((sql, materialized))
            return self.connection.executemany(sql, materialized)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.connection, name)

    recording = RecordingConnection(accumulator.connection)
    setattr(accumulator, "connection", recording)  # noqa: B010
    polygon = {"osm_type": "way", "osm_id": 1, "osm_version": 1}
    rows = [
        _asset_row(
            "region.osm.pbf",
            provider_asset_id=value,
            source_tag_value=f"https://example.test/{value}.jpg",
            canonical_reference=f"https://example.test/{value}.jpg",
            image_url=f"https://example.test/{value}.jpg",
        )
        for value in ("a", "b", "c")
    ]
    rows.sort(
        key=lambda row: public_assets_module._digest(public_assets_module.image_identity(row)),
        reverse=True,
    )
    try:
        accumulator.add_many([(row, polygon) for row in rows])
        writes = {
            "images": next(
                values for sql, values in recording.calls if "INSERT INTO images" in sql
            ),
            "links": next(
                values for sql, values in recording.calls if "INSERT OR IGNORE INTO links" in sql
            ),
        }
        assert writes["images"] == sorted(writes["images"], key=lambda values: values[0])
        assert writes["links"] == sorted(writes["links"], key=lambda values: values[0])
    finally:
        accumulator.close()


def test_asset_accumulator_deduplicates_repeated_batch_keys(tmp_path: Path) -> None:
    accumulator = public_assets_module._Accumulator(tmp_path / "assets.sqlite")

    class RecordingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection
            self.calls: list[tuple[str, list[tuple[object, ...]]]] = []

        def executemany(self, sql: str, values: Iterable[tuple[object, ...]]) -> sqlite3.Cursor:
            materialized = list(values)
            self.calls.append((sql, materialized))
            return self.connection.executemany(sql, materialized)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.connection, name)

    recording = RecordingConnection(accumulator.connection)
    setattr(accumulator, "connection", recording)  # noqa: B010
    row = _asset_row("region.osm.pbf")
    polygon = {"osm_type": "way", "osm_id": 1, "osm_version": 1}
    try:
        accumulator.add_many([(row, polygon), (row, polygon)])
        counts = {
            "images": next(
                len(values) for sql, values in recording.calls if "INSERT INTO images" in sql
            ),
            "image_sources": next(
                len(values)
                for sql, values in recording.calls
                if "INSERT OR IGNORE INTO image_sources" in sql
            ),
            "links": next(
                len(values)
                for sql, values in recording.calls
                if "INSERT OR IGNORE INTO links" in sql
            ),
            "link_sources": next(
                len(values)
                for sql, values in recording.calls
                if "INSERT OR IGNORE INTO link_sources" in sql
            ),
            "link_versions": next(
                len(values)
                for sql, values in recording.calls
                if "INSERT OR IGNORE INTO link_versions" in sql
            ),
        }
        assert counts == {
            "images": 1,
            "image_sources": 1,
            "links": 1,
            "link_sources": 1,
            "link_versions": 1,
        }
    finally:
        accumulator.close()


def test_asset_accumulator_streams_rows_without_length_hint(tmp_path: Path) -> None:
    accumulator = public_assets_module._Accumulator(tmp_path / "assets.sqlite")
    row = _asset_row("region.osm.pbf")
    polygon: dict[str, object] = {"osm_type": "way", "osm_id": 1, "osm_version": 1}

    class StreamingRows:
        def __iter__(self) -> Iterator[tuple[dict[str, object], dict[str, object]]]:
            yield row, polygon

        def __len__(self) -> int:
            raise AssertionError("add_many must not materialize the input iterable")

    try:
        accumulator.add_many(StreamingRows())
        assert accumulator.input_rows == 1
        assert accumulator.counts() == (1, 1)
    finally:
        accumulator.close()


def test_asset_accumulator_keeps_best_duplicate_image_row(tmp_path: Path) -> None:
    accumulator = public_assets_module._Accumulator(tmp_path / "assets.sqlite")
    base = _asset_row("region.osm.pbf")
    weak = base | {"status": "temporary_failure", "author": "z"}
    strong = base | {"author": "z"}
    tie_break = base | {"author": "a"}
    polygon = {"osm_type": "way", "osm_id": 1, "osm_version": 1}
    try:
        accumulator.add_many([(weak, polygon), (strong, polygon), (tie_break, polygon)])
        images = list(accumulator.images())
        assert len(images) == 1
        assert images[0]["author"] == "a"
    finally:
        accumulator.close()


def test_public_dataset_is_reused_when_inputs_are_unchanged(tmp_path: Path) -> None:
    _write_polygon_manifest(tmp_path, "region.osm.pbf", [_polygon_row("region.osm.pbf")])
    first = build_public_dataset(tmp_path)
    polygon_before = first.polygon_path.read_bytes()
    second = build_public_dataset(tmp_path)

    assert second.reused is True
    assert second.polygon_path.read_bytes() == polygon_before


def test_public_dataset_removes_obsolete_image_asset_after_materialization(
    tmp_path: Path,
) -> None:
    _write_polygon_manifest(tmp_path, "region.osm.pbf", [_polygon_row("region.osm.pbf")])
    legacy = tmp_path / LEGACY_PUBLIC_ASSET_RELATIVE
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"obsolete")

    build_public_dataset(tmp_path)
    assert not legacy.exists()

    legacy.write_bytes(b"obsolete")
    reused = build_public_dataset(tmp_path)
    assert reused.reused is True
    assert not legacy.exists()


def test_public_dataset_keeps_latest_object_and_unique_images_with_links(tmp_path: Path) -> None:
    _write_polygon_manifest(
        tmp_path,
        "old-region.osm.pbf",
        [_polygon_row("old-region.osm.pbf", osm_version=1)],
    )
    _write_polygon_manifest(
        tmp_path,
        "new-region.osm.pbf",
        [
            _polygon_row("new-region.osm.pbf", osm_version=2),
            _polygon_row("new-region.osm.pbf", osm_id=2),
        ],
    )
    shared = "https://cdn.example.test/same.jpg"
    _write_asset_manifest(
        tmp_path,
        "old-region.osm.pbf",
        "data/old-region.parquet",
        [
            _asset_row(
                "old-region.osm.pbf",
                osm_version=1,
                source_tag_value=shared,
                canonical_reference=shared,
                image_url=shared,
            )
        ],
    )
    _write_asset_manifest(
        tmp_path,
        "new-region.osm.pbf",
        "data/new-region.parquet",
        [
            _asset_row(
                "new-region.osm.pbf",
                osm_version=2,
                source_tag_value=shared,
                canonical_reference=shared,
                image_url=shared,
            ),
            _asset_row(
                "new-region.osm.pbf",
                osm_id=2,
                source_tag_key="wikimedia_commons",
                source_tag_value="File:Same.jpg",
                canonical_reference="File:Same.jpg",
                provider_asset_id="same-file",
                relation_kind="category_membership",
                image_url=shared,
            ),
        ],
    )

    result = build_public_dataset(tmp_path)

    assert result.polygon_rows == 2
    assert result.duplicate_polygon_rows == 1
    assert result.image_rows == 1
    assert result.link_rows == 2
    assert result.duplicate_image_rows == 2
    assert result.duplicate_link_rows == 1
    assert result.image_path.relative_to(tmp_path).as_posix() == PUBLIC_IMAGE_RELATIVE
    assert result.link_path.relative_to(tmp_path).as_posix() == PUBLIC_LINK_RELATIVE

    import pyarrow.parquet as pq

    polygons = pq.read_table(result.polygon_path).to_pylist()
    images = pq.read_table(result.image_path).to_pylist()
    links = pq.read_table(result.link_path).to_pylist()
    current = next(row for row in polygons if row["osm_id"] == 1)
    assert current["osm_version"] == 2
    assert current["source_pbfs"] == ["new-region.osm.pbf", "old-region.osm.pbf"]
    assert images[0]["image_url"] == shared
    assert {row["osm_id"] for row in links} == {1, 2}
    assert next(row for row in links if row["osm_id"] == 1)["source_pbfs"] == [
        "new-region.osm.pbf",
        "old-region.osm.pbf",
    ]
    assert next(row for row in links if row["osm_id"] == 1)["observed_osm_versions"] == [1, 2]

    reused = build_public_dataset(tmp_path)
    assert reused.reused is True
    assert reused.image_path.read_bytes() == result.image_path.read_bytes()
    assert reused.link_path.read_bytes() == result.link_path.read_bytes()

    statistics = public_asset_statistics(
        result.image_path,
        result.link_path,
        verified_asset_manifests(tmp_path),
        duplicate_images=result.duplicate_image_rows,
        duplicate_links=result.duplicate_link_rows,
    )
    assert statistics["rows"] == 1
    assert statistics["relationship_rows"] == 2
    assert statistics["direct_urls"] == 1
    assert statistics["usable_relationship_rows"] == 2
    assert statistics["image_relation_counts"] == {
        "category_membership": 1,
        "direct_reference": 1,
    }

    default_statistics = public_asset_statistics(
        result.image_path,
        result.link_path,
        verified_asset_manifests(tmp_path),
    )
    assert default_statistics["duplicate_assets"] == 0
    assert default_statistics["duplicate_assets_removed"] == 0
    assert default_statistics["duplicate_images_removed"] == 0
    assert default_statistics["duplicate_links_removed"] == 0
    assert default_statistics["orphan_rows"] == 0
