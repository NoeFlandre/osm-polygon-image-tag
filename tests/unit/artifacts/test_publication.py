import json
from pathlib import Path

import pytest

from osm_polygon_image_tag.artifacts.publication import (
    EXPECTED_REPO,
    publish_dataset,
)
from osm_polygon_image_tag.artifacts.publication_inventory import publication_inventory
from osm_polygon_image_tag.artifacts.publication_types import HubCommit
from osm_polygon_image_tag.artifacts.reporting import generate_metadata
from osm_polygon_image_tag.artifacts.storage import write_geoparquet
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
from osm_polygon_image_tag.assets.storage import write_asset_parquet
from osm_polygon_image_tag.core.errors import PublicationError
from osm_polygon_image_tag.core.manifest import (
    DATASET_SCHEMA_VERSION,
    PROCESSING_CONTRACT_VERSION,
    Manifest,
    OutputIdentity,
    RunCounts,
    SourceIdentity,
    file_sha256,
    read_manifest,
    write_manifest,
)


def _dataset(root: Path) -> None:
    data = root / "data" / "region.parquet"
    write_geoparquet([], data)
    manifest = Manifest(
        manifest_schema_version=1,
        processing_contract_version=PROCESSING_CONTRACT_VERSION,
        dataset_schema_version=DATASET_SCHEMA_VERSION,
        source=SourceIdentity("region.osm.pbf", 1, 1, "a" * 64),
        output=OutputIdentity(
            "data/region.parquet",
            data.stat().st_size,
            file_sha256(data),
            0,
        ),
        osmium_version="test",
        counts=RunCounts(0, {}),
    )
    write_manifest(manifest, root / "manifests" / "region.manifest.json")
    generate_metadata(root)


def _asset_dataset(root: Path) -> None:
    output = root / "assets" / "region.assets.parquet"
    write_asset_parquet([], output)
    manifest = AssetManifest(
        ASSET_MANIFEST_SCHEMA_VERSION,
        ASSET_SCHEMA_VERSION,
        RESOLVER_CONTRACT_VERSION,
        AssetSourceIdentity("data/region.parquet", 1, "a" * 64, 0),
        ResolutionSnapshotIdentity(0, "b" * 64),
        OutputIdentity(
            "assets/region.assets.parquet",
            output.stat().st_size,
            file_sha256(output),
            0,
        ),
        AssetRunCounts(0, {}, {}, 0, 0, 0),
    )
    write_asset_manifest(
        manifest,
        root / "asset-manifests" / "region.assets.manifest.json",
    )


class _FakeHub:
    def __init__(self, *, corrupt: bool = False) -> None:
        self.commits: list[HubCommit] = []
        self.files: dict[str, bytes] = {}
        self.corrupt = corrupt

    def commit(self, commit: HubCommit) -> str:
        self.commits.append(commit)
        for removed in commit.deletions:
            self.files.pop(removed, None)
        self.files.update({item.remote_path: item.local_path.read_bytes() for item in commit.files})
        return f"commit-{len(self.commits)}"

    def download(self, repo_id: str, remote_path: str, revision: str) -> bytes:
        assert repo_id == EXPECTED_REPO
        assert revision.startswith("commit-")
        content = self.files[remote_path]
        return b"corrupt" if self.corrupt and remote_path.endswith(".parquet") else content


def test_inventory_contains_only_verified_public_artifacts(tmp_path: Path) -> None:
    _dataset(tmp_path)
    _asset_dataset(tmp_path)
    (tmp_path / ".DS_Store").write_bytes(b"finder")

    inventory = publication_inventory(tmp_path)

    assert [item.remote_path for item in inventory] == [
        "README.md",
        "asset-manifests/region.assets.manifest.json",
        "assets/geographic_polygon_density.png",
        "assets/hero.png",
        "assets/region.assets.parquet",
        "data/region.parquet",
        "manifests/region.manifest.json",
        "statistics/dataset-statistics.json",
    ]


def test_inventory_keeps_cache_and_retry_state_private(tmp_path: Path) -> None:
    _dataset(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    (cache / "resolutions.sqlite").write_bytes(b"private")
    (cache / "retry-state.json").write_bytes(b"private")

    inventory = publication_inventory(tmp_path)

    assert all(not item.remote_path.startswith("cache/") for item in inventory)


def test_inventory_rejects_invalid_hero_png(tmp_path: Path) -> None:
    _dataset(tmp_path)
    (tmp_path / "assets/hero.png").write_bytes(b"not a PNG")

    with pytest.raises(PublicationError, match="invalid hero PNG"):
        publication_inventory(tmp_path)


def test_inventory_rejects_noncanonical_hero_png(tmp_path: Path) -> None:
    _dataset(tmp_path)
    (tmp_path / "assets/hero.png").write_bytes(b"\x89PNG\r\n\x1a\nother")

    with pytest.raises(PublicationError, match="hero PNG does not match packaged resource"):
        publication_inventory(tmp_path)


def test_inventory_rejects_same_size_asset_corruption(tmp_path: Path) -> None:
    _dataset(tmp_path)
    _asset_dataset(tmp_path)
    output = tmp_path / "assets" / "region.assets.parquet"
    content = bytearray(output.read_bytes())
    content[len(content) // 2] ^= 1
    output.write_bytes(content)

    with pytest.raises(PublicationError, match="asset digest"):
        publication_inventory(tmp_path)


def test_inventory_rejects_self_consistent_non_parquet_asset(tmp_path: Path) -> None:
    _dataset(tmp_path)
    _asset_dataset(tmp_path)
    output = tmp_path / "assets" / "region.assets.parquet"
    output.write_bytes(b"not a parquet file")
    manifest_path = tmp_path / "asset-manifests" / "region.assets.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["output"].update(
        {
            "size_bytes": output.stat().st_size,
            "sha256": file_sha256(output),
            "row_count": 0,
        }
    )
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PublicationError, match="asset Parquet"):
        publication_inventory(tmp_path)


def test_inventory_reuses_manifest_digest_for_parquet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dataset(tmp_path)
    original = file_sha256

    def hash_small_artifacts(path: Path) -> str:
        if path.suffix == ".parquet":
            raise AssertionError("publication rehashed a finalized Parquet shard")
        return original(path)

    monkeypatch.setattr(
        "osm_polygon_image_tag.artifacts.publication_inventory.file_sha256",
        hash_small_artifacts,
    )

    inventory = publication_inventory(tmp_path)

    parquet = next(item for item in inventory if item.remote_path.endswith(".parquet"))
    manifest = read_manifest(tmp_path / "manifests" / "region.manifest.json")
    assert parquet.sha256 == manifest.output.sha256


def test_inventory_ignores_managed_shards_from_old_contract_during_migration(
    tmp_path: Path,
) -> None:
    _dataset(tmp_path)
    stale_output = tmp_path / "data" / "stale.parquet"
    stale_output.write_bytes(b"old-schema")
    write_manifest(
        Manifest(
            manifest_schema_version=1,
            processing_contract_version=1,
            dataset_schema_version=1,
            source=SourceIdentity("stale.osm.pbf", 1, 1, "b" * 64),
            output=OutputIdentity(
                "data/stale.parquet",
                stale_output.stat().st_size,
                file_sha256(stale_output),
                1,
            ),
            osmium_version="old",
            counts=RunCounts(1, {}),
        ),
        tmp_path / "manifests" / "stale.manifest.json",
    )

    inventory = publication_inventory(tmp_path)

    assert "data/stale.parquet" not in {item.remote_path for item in inventory}
    assert "manifests/stale.manifest.json" not in {item.remote_path for item in inventory}


def test_inventory_ignores_managed_asset_shards_from_old_contract(
    tmp_path: Path,
) -> None:
    _dataset(tmp_path)
    _asset_dataset(tmp_path)
    manifest_path = tmp_path / "asset-manifests" / "region.assets.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["asset_schema_version"] = ASSET_SCHEMA_VERSION - 1
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    inventory = publication_inventory(tmp_path)
    remote_paths = {item.remote_path for item in inventory}

    assert "assets/region.assets.parquet" not in remote_paths
    assert "asset-manifests/region.assets.manifest.json" not in remote_paths


def test_old_contract_manifest_cannot_escape_data_root(tmp_path: Path) -> None:
    _dataset(tmp_path)
    outside = tmp_path.parent / "outside.parquet"
    outside.write_bytes(b"outside")
    write_manifest(
        Manifest(
            manifest_schema_version=1,
            processing_contract_version=1,
            dataset_schema_version=1,
            source=SourceIdentity("stale.osm.pbf", 1, 1, "c" * 64),
            output=OutputIdentity(
                "../outside.parquet",
                outside.stat().st_size,
                file_sha256(outside),
                1,
            ),
            osmium_version="old",
            counts=RunCounts(1, {}),
        ),
        tmp_path / "manifests" / "escape.manifest.json",
    )

    with pytest.raises(PublicationError, match="escapes data root"):
        publication_inventory(tmp_path)


@pytest.mark.parametrize("unexpected", ["secret.txt", "catalog/extra.txt"])
def test_inventory_rejects_unexpected_entries(tmp_path: Path, unexpected: str) -> None:
    _dataset(tmp_path)
    path = tmp_path / unexpected
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("no")

    with pytest.raises(PublicationError, match="unexpected"):
        publication_inventory(tmp_path)


def test_inventory_preserves_and_rejects_atomic_write_temporary_files(tmp_path: Path) -> None:
    _dataset(tmp_path)
    partial = tmp_path / "data" / ".baden-wuerttemberg-latest.parquet.tmp"
    partial.write_bytes(b"interrupted write")

    with pytest.raises(PublicationError, match="unexpected"):
        publication_inventory(tmp_path)

    assert partial.read_bytes() == b"interrupted write"


def test_inventory_preserves_and_rejects_unknown_temporary_files(tmp_path: Path) -> None:
    _dataset(tmp_path)
    temporary_root = tmp_path / "tmp"
    temporary_root.mkdir(exist_ok=True)
    tag_store = temporary_root / "tag-store-possibly-active.sqlite"
    tag_store.write_bytes(b"unknown ownership")

    with pytest.raises(PublicationError, match="unexpected"):
        publication_inventory(tmp_path)

    assert tag_store.read_bytes() == b"unknown ownership"


def test_inventory_rejects_symlinked_tmp_without_deleting_external_files(
    tmp_path: Path,
) -> None:
    _dataset(tmp_path)
    external = tmp_path.parent / f"{tmp_path.name}-external"
    external.mkdir()
    protected = external / "must-survive.tmp"
    protected.write_bytes(b"user data")
    (tmp_path / "tmp").symlink_to(external, target_is_directory=True)

    with pytest.raises(PublicationError, match="symlink"):
        publication_inventory(tmp_path)

    assert protected.read_bytes() == b"user data"


def test_inventory_rejects_symlinks(tmp_path: Path) -> None:
    _dataset(tmp_path)
    (tmp_path / "README.md").unlink()
    (tmp_path / "README.md").symlink_to(tmp_path / "statistics/dataset-statistics.json")

    with pytest.raises(PublicationError, match="symlink"):
        publication_inventory(tmp_path)


def test_publish_verifies_remote_content_and_resumes_from_receipt(tmp_path: Path) -> None:
    _dataset(tmp_path)
    hub = _FakeHub()

    first = publish_dataset(tmp_path, confirm_repo=EXPECTED_REPO, hub=hub)
    second = publish_dataset(tmp_path, confirm_repo=EXPECTED_REPO, hub=hub)

    assert first.status == "published"
    assert second.status == "skipped"
    assert len(hub.commits) == 1
    receipt = json.loads((tmp_path / "receipts/publication.json").read_text())
    assert receipt["commit_id"] == "commit-1"
    assert receipt["repo_id"] == EXPECTED_REPO


def test_publish_requires_exact_repo_confirmation(tmp_path: Path) -> None:
    _dataset(tmp_path)

    with pytest.raises(PublicationError, match="confirmation"):
        publish_dataset(tmp_path, confirm_repo="other/repo", hub=_FakeHub())


def test_publish_does_not_write_receipt_when_remote_digest_differs(tmp_path: Path) -> None:
    _dataset(tmp_path)

    with pytest.raises(PublicationError, match="remote digest"):
        publish_dataset(tmp_path, confirm_repo=EXPECTED_REPO, hub=_FakeHub(corrupt=True))

    assert not (tmp_path / "receipts/publication.json").exists()


def test_next_publication_commits_and_verifies_only_changed_files(tmp_path: Path) -> None:
    _dataset(tmp_path)
    hub = _FakeHub()
    publish_dataset(tmp_path, confirm_repo=EXPECTED_REPO, hub=hub)
    (tmp_path / "README.md").write_text("changed")

    result = publish_dataset(tmp_path, confirm_repo=EXPECTED_REPO, hub=hub)

    assert result.status == "published"
    assert [item.remote_path for item in hub.commits[1].files] == ["README.md"]


def test_invalid_receipt_is_rejected_without_hub_write(tmp_path: Path) -> None:
    _dataset(tmp_path)
    receipt = tmp_path / "receipts/publication.json"
    receipt.parent.mkdir()
    receipt.write_text('{"files":"not-a-list"}')
    hub = _FakeHub()

    with pytest.raises(PublicationError, match="invalid publication receipt"):
        publish_dataset(tmp_path, confirm_repo=EXPECTED_REPO, hub=hub)

    assert hub.commits == []
