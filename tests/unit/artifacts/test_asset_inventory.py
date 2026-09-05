import json
from pathlib import Path

import pytest

from osm_polygon_image_tag.artifacts.asset_inventory import verified_asset_manifests
from osm_polygon_image_tag.assets.manifest import (
    ASSET_MANIFEST_SCHEMA_VERSION,
    AssetManifest,
    AssetRunCounts,
    AssetSourceIdentity,
    ResolutionSnapshotIdentity,
    write_asset_manifest,
)
from osm_polygon_image_tag.assets.schema import (
    ASSET_SCHEMA_VERSION,
    RESOLVER_CONTRACT_VERSION,
)
from osm_polygon_image_tag.core.manifest import OutputIdentity


def asset_fixture(tmp_path: Path, *, relative: str = "assets/region.assets.parquet"):
    unsafe = Path(relative).is_absolute() or ".." in Path(relative).parts
    output = tmp_path / ("placeholder.parquet" if unsafe else relative)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"asset")
    manifest = AssetManifest(
        ASSET_MANIFEST_SCHEMA_VERSION,
        ASSET_SCHEMA_VERSION,
        RESOLVER_CONTRACT_VERSION,
        AssetSourceIdentity("data/region.parquet", 10, "a" * 64, 1),
        ResolutionSnapshotIdentity(1, "b" * 64),
        OutputIdentity(relative, output.stat().st_size, "c" * 64, 2),
        AssetRunCounts(
            2,
            {"resolved": 1, "temporary_failure": 1},
            {"panoramax": 2},
            1,
            0,
            1,
        ),
    )
    manifest_path = tmp_path / "asset-manifests" / "region.assets.manifest.json"
    write_asset_manifest(manifest, manifest_path)
    return manifest, output, manifest_path


def test_asset_inventory_selects_compatible_bound_outputs_with_pending_counts(
    tmp_path: Path,
) -> None:
    manifest, output, _path = asset_fixture(tmp_path)
    events: list[dict[str, object]] = []

    selected = verified_asset_manifests(tmp_path, progress=events.append)

    assert selected == [(manifest, output.resolve())]
    assert selected[0][0].counts.pending_retries == 1
    assert events[-1]["verified_shards"] == 1
    assert events[-1]["pending_retries"] == 1
    assert verified_asset_manifests(tmp_path) == selected


@pytest.mark.parametrize("relative", ["../escape.parquet", "/absolute.parquet"])
def test_asset_inventory_rejects_path_escape(tmp_path: Path, relative: str) -> None:
    asset_fixture(tmp_path, relative=relative)

    with pytest.raises(ValueError, match="escapes data root"):
        verified_asset_manifests(tmp_path)


def test_asset_inventory_rejects_size_mismatch(tmp_path: Path) -> None:
    _manifest, output, _path = asset_fixture(tmp_path)
    output.write_bytes(b"changed")

    with pytest.raises(ValueError, match="identity mismatch"):
        verified_asset_manifests(tmp_path)


def test_asset_inventory_rejects_symlinked_output(tmp_path: Path) -> None:
    _manifest, output, _path = asset_fixture(tmp_path)
    external = tmp_path / "external.parquet"
    external.write_bytes(output.read_bytes())
    output.unlink()
    output.symlink_to(external)

    with pytest.raises(ValueError, match="symlink"):
        verified_asset_manifests(tmp_path)


def test_asset_inventory_skips_manifest_from_old_asset_contract(tmp_path: Path) -> None:
    _manifest, _output, manifest_path = asset_fixture(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["asset_schema_version"] = ASSET_SCHEMA_VERSION - 1
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert verified_asset_manifests(tmp_path) == []
