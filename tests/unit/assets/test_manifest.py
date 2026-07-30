import json
from dataclasses import replace
from pathlib import Path

import pytest

from osm_polygon_image_tag.assets.manifest import (
    ASSET_MANIFEST_SCHEMA_VERSION,
    AssetManifest,
    AssetManifestError,
    AssetRunCounts,
    AssetSourceIdentity,
    ResolutionSnapshotIdentity,
    read_asset_manifest,
    write_asset_manifest,
)
from osm_polygon_image_tag.assets.schema import (
    ASSET_SCHEMA_VERSION,
    RESOLVER_CONTRACT_VERSION,
)
from osm_polygon_image_tag.core.manifest import OutputIdentity


def _manifest() -> AssetManifest:
    return AssetManifest(
        manifest_schema_version=ASSET_MANIFEST_SCHEMA_VERSION,
        asset_schema_version=ASSET_SCHEMA_VERSION,
        resolver_contract_version=RESOLVER_CONTRACT_VERSION,
        source=AssetSourceIdentity(
            relative_path="data/region.parquet",
            size_bytes=101,
            sha256="a" * 64,
            row_count=3,
        ),
        resolution_snapshot=ResolutionSnapshotIdentity(entry_count=2, sha256="b" * 64),
        output=OutputIdentity(
            relative_path="assets/region.assets.parquet",
            size_bytes=202,
            sha256="c" * 64,
            row_count=4,
        ),
        counts=AssetRunCounts(
            rows=4,
            statuses={"resolved": 3, "category_truncated": 1},
            providers={"panoramax": 1, "wikimedia_commons": 3},
            pending_retries=0,
            truncated_categories=1,
            direct_urls=3,
        ),
    )


def test_asset_manifest_is_canonical_atomic_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "asset-manifests" / "region.assets.manifest.json"
    manifest = _manifest()

    write_asset_manifest(manifest, path)
    first = path.read_bytes()
    reordered = replace(
        manifest,
        counts=replace(
            manifest.counts,
            statuses={"category_truncated": 1, "resolved": 3},
            providers={"wikimedia_commons": 3, "panoramax": 1},
        ),
    )
    write_asset_manifest(reordered, path)

    assert read_asset_manifest(path, data_root=tmp_path) == manifest
    assert path.read_bytes() == first
    assert first.endswith(b"\n")
    assert not list(path.parent.glob("*.tmp"))


def test_asset_manifest_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "manifest_schema_version": 1,
                "asset_schema_version": 1,
                "resolver_contract_version": 1,
                "source": {},
                "resolution_snapshot": {},
                "output": {},
                "counts": {},
                "surprise": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AssetManifestError, match="invalid manifest fields"):
        read_asset_manifest(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("manifest_schema_version", 99, "manifest schema"),
        ("asset_schema_version", 99, "asset schema"),
        ("resolver_contract_version", 99, "resolver contract"),
    ],
)
def test_asset_manifest_rejects_incompatible_contracts(
    tmp_path: Path, field: str, value: int, message: str
) -> None:
    path = tmp_path / "manifest.json"
    write_asset_manifest(replace(_manifest(), **{field: value}), path)

    with pytest.raises(AssetManifestError, match=message):
        read_asset_manifest(path)


@pytest.mark.parametrize(
    "relative_path",
    ["../escape.parquet", "/absolute/escape.parquet", "assets/../../escape.parquet"],
)
def test_asset_manifest_rejects_paths_outside_data_root(
    tmp_path: Path, relative_path: str
) -> None:
    path = tmp_path / "manifest.json"
    manifest = replace(
        _manifest(),
        output=replace(_manifest().output, relative_path=relative_path),
    )
    write_asset_manifest(manifest, path)

    with pytest.raises(AssetManifestError, match="outside data root"):
        read_asset_manifest(path, data_root=tmp_path)


def test_asset_manifest_rejects_unknown_status(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    manifest = replace(
        _manifest(),
        counts=replace(_manifest().counts, statuses={"invented": 4}),
    )
    write_asset_manifest(manifest, path)

    with pytest.raises(AssetManifestError, match="unsupported asset status"):
        read_asset_manifest(path)
