from pathlib import Path

from osm_polygon_image_tag.artifacts.public_asset_checkpoint import (
    _checkpoint_family,
    _checkpoint_limit,
    _checkpoint_root_overlaps,
    _checkpoint_source_is_valid,
)
from osm_polygon_image_tag.artifacts.public_asset_schema import (
    PUBLIC_IMAGE_SCHEMA_VERSION,
    PUBLIC_LINK_SCHEMA_VERSION,
    public_image_schema,
    public_link_schema,
)


def test_public_asset_schema_module_owns_stable_contracts() -> None:
    assert PUBLIC_IMAGE_SCHEMA_VERSION == 1
    assert PUBLIC_LINK_SCHEMA_VERSION == 1
    assert public_image_schema().names[-1] == "source_pbfs"
    assert public_link_schema().names[-1] == "observed_osm_versions"
    assert public_image_schema().metadata == {
        b"osm_polygon_image_tag_public_image_schema_version": b"1"
    }
    assert public_link_schema().metadata == {
        b"osm_polygon_image_tag_public_link_schema_version": b"1"
    }


def test_checkpoint_module_keeps_path_and_limit_decisions(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.sqlite"
    assert _checkpoint_family(checkpoint) == (
        checkpoint,
        Path(f"{checkpoint}-journal"),
        Path(f"{checkpoint}-wal"),
        Path(f"{checkpoint}-shm"),
    )
    assert _checkpoint_root_overlaps(tmp_path / "nested", tmp_path)
    assert not _checkpoint_root_overlaps(tmp_path.parent / "scratch", tmp_path)
    assert _checkpoint_limit(20 * 1024**3, 0) == (20 * 1024**3 - 8 * 1024**3) // 2
    assert _checkpoint_source_is_valid(0, "a", 2, 1, ("a",))
    assert not _checkpoint_source_is_valid(1, "a", 2, 1, ("a",))
    assert not _checkpoint_source_is_valid(0, "a", 2, 3, ("a",))
