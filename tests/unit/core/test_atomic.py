from pathlib import Path

import pytest

from osm_polygon_image_tag.core import atomic
from osm_polygon_image_tag.core.atomic import atomic_write_bytes


def test_atomic_write_bytes_creates_parent_and_replaces_destination(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "artifact.json"

    atomic_write_bytes(path, b"payload", prefix=".artifact.", suffix=".tmp")

    assert path.read_bytes() == b"payload"
    assert not list(path.parent.glob(".artifact.*.tmp"))


def test_atomic_write_bytes_cleans_temporary_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "artifact.json"
    path.write_bytes(b"previous")

    def fail_replace(_source: str | Path, _destination: str | Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(atomic.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_write_bytes(path, b"replacement", prefix=".artifact.", suffix=".tmp")

    assert path.read_bytes() == b"previous"
    assert not list(path.parent.glob(".artifact.*.tmp"))


def test_atomic_write_bytes_can_sync_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"

    atomic_write_bytes(path, b"payload", sync_directory=True)

    assert path.read_bytes() == b"payload"
