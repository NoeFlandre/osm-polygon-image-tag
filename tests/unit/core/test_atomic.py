from pathlib import Path

import pytest

from osm_polygon_image_tag.core import atomic
from osm_polygon_image_tag.core.atomic import atomic_write_bytes


def test_temporary_path_uses_stable_default_name_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    class FakeTemporary:
        name = str(tmp_path / "tmp-random")

        def __enter__(self) -> "FakeTemporary":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_named_temporary_file(**kwargs: object) -> FakeTemporary:
        calls.append(kwargs)
        return FakeTemporary()

    monkeypatch.setattr(atomic.tempfile, "NamedTemporaryFile", fake_named_temporary_file)

    owner = atomic.TemporaryPath(tmp_path)
    owner.close()

    assert calls == [{"prefix": "tmp", "suffix": "", "dir": tmp_path, "delete": False}]


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
    temporary = tmp_path / ".artifact.tmp"
    destination = tmp_path / "artifact.json"
    temporary.write_bytes(b"payload")
    destination.write_bytes(b"previous")

    atomic.promote_temporary_file(temporary, destination, sync_directory=True)

    assert destination.read_bytes() == b"payload"
    assert not temporary.exists()


def test_promote_temporary_file_does_not_sync_directory_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temporary = tmp_path / ".artifact.tmp"
    destination = tmp_path / "artifact.json"
    temporary.write_bytes(b"payload")
    calls: list[Path] = []
    monkeypatch.setattr(atomic, "_sync_directory", calls.append)

    atomic.promote_temporary_file(temporary, destination)

    assert destination.read_bytes() == b"payload"
    assert calls == []


def test_atomic_write_bytes_uses_stable_default_temporary_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "artifact.json"
    temporary = tmp_path / "fake-temp"
    options: dict[str, object] = {}

    class FakeTemporaryContext:
        def __enter__(self) -> Path:
            return temporary

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_temporary_file_path(
        _directory: Path, *, prefix: str, suffix: str
    ) -> FakeTemporaryContext:
        options.update(prefix=prefix, suffix=suffix)
        return FakeTemporaryContext()

    def fake_promote(temporary_path: Path, final_path: Path, *, sync_directory: bool) -> None:
        options["sync_directory"] = sync_directory
        final_path.write_bytes(temporary_path.read_bytes())
        temporary_path.unlink()

    monkeypatch.setattr(atomic, "temporary_file_path", fake_temporary_file_path)
    monkeypatch.setattr(atomic, "promote_temporary_file", fake_promote)

    atomic_write_bytes(path, b"payload")

    assert path.read_bytes() == b"payload"
    assert options == {"prefix": "tmp", "suffix": "", "sync_directory": False}


def test_promote_temporary_file_cleans_source_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temporary = tmp_path / ".artifact.tmp"
    destination = tmp_path / "artifact.json"
    temporary.write_bytes(b"payload")
    destination.write_bytes(b"previous")

    def fail_replace(_source: str | Path, _destination: str | Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(atomic.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic.promote_temporary_file(temporary, destination)

    assert destination.read_bytes() == b"previous"
    assert not temporary.exists()


def test_temporary_file_path_yields_named_path_and_cleans_it(tmp_path: Path) -> None:
    with atomic.temporary_file_path(tmp_path, prefix=".artifact.", suffix=".tmp") as path:
        assert path.parent == tmp_path
        assert path.name.startswith(".artifact.")
        assert path.name.endswith(".tmp")
        path.write_bytes(b"payload")
        assert path.exists()

    assert not path.exists()


def test_temporary_file_path_cleans_up_when_scope_raises(tmp_path: Path) -> None:
    with (
        pytest.raises(RuntimeError, match="scope failed"),
        atomic.temporary_file_path(tmp_path) as path,
    ):
        path.write_bytes(b"payload")
        raise RuntimeError("scope failed")

    assert not path.exists()


def test_temporary_path_owns_file_until_close(tmp_path: Path) -> None:
    owner = atomic.TemporaryPath(tmp_path, prefix=".asset.", suffix=".sqlite")
    path = owner.path

    assert path.parent == tmp_path
    assert path.name.startswith(".asset.")
    assert path.name.endswith(".sqlite")
    assert path.exists()

    owner.close()

    assert not path.exists()


def test_temporary_path_close_is_idempotent(tmp_path: Path) -> None:
    owner = atomic.TemporaryPath(tmp_path)
    owner.close()
    owner.close()

    assert not owner.path.exists()


def test_temporary_path_context_cleans_up(tmp_path: Path) -> None:
    with atomic.TemporaryPath(tmp_path) as path:
        assert path.exists()

    assert not path.exists()
