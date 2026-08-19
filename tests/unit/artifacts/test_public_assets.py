"""Tests for bounded, resumable public-asset checkpoint selection."""

from pathlib import Path

import pytest

import osm_polygon_image_tag.artifacts.public_assets as public_assets
from osm_polygon_image_tag.artifacts.public_assets import (
    PUBLIC_ASSET_CHECKPOINT_FILENAME,
    PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE,
    _prepare_checkpoint_paths,
)


def _external_root(tmp_path: Path) -> Path:
    return tmp_path.parent / f"{tmp_path.name}-scratch"


def test_checkpoint_without_external_root_uses_durable_path(tmp_path: Path) -> None:
    durable = tmp_path / PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE

    assert _prepare_checkpoint_paths(tmp_path, None) == (durable, (durable,))


def test_checkpoint_rejects_symlinked_external_root(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "checkpoint-link"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="root must not be a symlink"):
        _prepare_checkpoint_paths(tmp_path, link)


@pytest.mark.parametrize("relative", [".", "nested", ".."])
def test_checkpoint_rejects_roots_overlapping_data_root(tmp_path: Path, relative: str) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()

    with pytest.raises(ValueError, match="separate from the data root"):
        _prepare_checkpoint_paths(data_root, data_root / relative)


def test_checkpoint_rejects_symlinked_external_file(tmp_path: Path) -> None:
    scratch = _external_root(tmp_path)
    scratch.mkdir()
    external = tmp_path / "external.sqlite"
    external.write_bytes(b"checkpoint")
    (scratch / PUBLIC_ASSET_CHECKPOINT_FILENAME).symlink_to(external)

    with pytest.raises(ValueError, match="file must not be a symlink"):
        _prepare_checkpoint_paths(tmp_path / "data", scratch)


def test_checkpoint_seeds_external_copy_from_durable_checkpoint(tmp_path: Path) -> None:
    durable = tmp_path / PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE
    durable.parent.mkdir(parents=True)
    durable.write_bytes(b"durable checkpoint")
    scratch = _external_root(tmp_path)

    active, cleanup = _prepare_checkpoint_paths(tmp_path, scratch)

    expected = scratch / PUBLIC_ASSET_CHECKPOINT_FILENAME
    assert active == expected
    assert cleanup == (expected, durable)
    assert expected.read_bytes() == durable.read_bytes()


def test_checkpoint_prefers_existing_external_copy(tmp_path: Path) -> None:
    durable = tmp_path / PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE
    durable.parent.mkdir(parents=True)
    durable.write_bytes(b"durable checkpoint")
    scratch = _external_root(tmp_path)
    external = scratch / PUBLIC_ASSET_CHECKPOINT_FILENAME
    external.parent.mkdir()
    external.write_bytes(b"external checkpoint")

    active, cleanup = _prepare_checkpoint_paths(tmp_path, scratch)

    assert active == external
    assert cleanup == (external, durable)


def test_checkpoint_keeps_durable_copy_when_sqlite_journal_exists(tmp_path: Path) -> None:
    durable = tmp_path / PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE
    durable.parent.mkdir(parents=True)
    durable.write_bytes(b"durable checkpoint")
    (durable.parent / f"{durable.name}-wal").write_bytes(b"journal")
    scratch = _external_root(tmp_path)

    active, cleanup = _prepare_checkpoint_paths(tmp_path, scratch)

    assert active == durable
    assert cleanup == (durable, scratch / PUBLIC_ASSET_CHECKPOINT_FILENAME)


def test_checkpoint_seed_calls_storage_limit_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    durable = tmp_path / PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE
    durable.parent.mkdir(parents=True)
    durable.write_bytes(b"durable checkpoint")
    scratch = _external_root(tmp_path)
    seen: list[tuple[Path, int]] = []

    monkeypatch.setattr(
        public_assets,
        "_checkpoint_max_bytes",
        lambda path, *, initial_bytes: seen.append((path, initial_bytes)) or 123,
    )

    _prepare_checkpoint_paths(tmp_path, scratch)

    assert seen == [(scratch / PUBLIC_ASSET_CHECKPOINT_FILENAME, len(b"durable checkpoint"))]
