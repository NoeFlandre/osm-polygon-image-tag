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


def test_promote_temporary_file_replaces_destination_and_removes_source(tmp_path: Path) -> None:
    promote = getattr(atomic, "promote_temporary_file", None)
    assert promote is not None
    temporary = tmp_path / ".artifact.tmp"
    destination = tmp_path / "artifact.json"
    temporary.write_bytes(b"payload")
    destination.write_bytes(b"previous")

    promote(temporary, destination, sync_directory=True)

    assert destination.read_bytes() == b"payload"
    assert not temporary.exists()


def test_promote_temporary_file_cleans_source_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    promote = getattr(atomic, "promote_temporary_file", None)
    assert promote is not None
    temporary = tmp_path / ".artifact.tmp"
    destination = tmp_path / "artifact.json"
    temporary.write_bytes(b"payload")
    destination.write_bytes(b"previous")

    def fail_replace(_source: str | Path, _destination: str | Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(atomic.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        promote(temporary, destination)

    assert destination.read_bytes() == b"previous"
    assert not temporary.exists()


def test_temporary_file_path_yields_named_path_and_cleans_it(tmp_path: Path) -> None:
    temporary_file_path = getattr(atomic, "temporary_file_path", None)
    assert temporary_file_path is not None

    with temporary_file_path(tmp_path, prefix=".artifact.", suffix=".tmp") as path:
        assert path.parent == tmp_path
        assert path.name.startswith(".artifact.")
        assert path.name.endswith(".tmp")
        path.write_bytes(b"payload")
        assert path.exists()

    assert not path.exists()


def test_temporary_file_path_cleans_up_when_scope_raises(tmp_path: Path) -> None:
    temporary_file_path = getattr(atomic, "temporary_file_path", None)
    assert temporary_file_path is not None

    with pytest.raises(RuntimeError, match="scope failed"), temporary_file_path(tmp_path) as path:
        path.write_bytes(b"payload")
        raise RuntimeError("scope failed")

    assert not path.exists()


def test_temporary_path_owns_file_until_close(tmp_path: Path) -> None:
    temporary_path = getattr(atomic, "TemporaryPath", None)
    assert temporary_path is not None

    owner = temporary_path(tmp_path, prefix=".asset.", suffix=".sqlite")
    path = owner.path

    assert path.parent == tmp_path
    assert path.name.startswith(".asset.")
    assert path.name.endswith(".sqlite")
    assert path.exists()

    owner.close()

    assert not path.exists()


def test_temporary_path_close_is_idempotent(tmp_path: Path) -> None:
    temporary_path = getattr(atomic, "TemporaryPath", None)
    assert temporary_path is not None

    owner = temporary_path(tmp_path)
    owner.close()
    owner.close()

    assert not owner.path.exists()


def test_temporary_path_context_cleans_up(tmp_path: Path) -> None:
    temporary_path = getattr(atomic, "TemporaryPath", None)
    assert temporary_path is not None

    with temporary_path(tmp_path) as path:
        assert path.exists()

    assert not path.exists()
