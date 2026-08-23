"""Durable atomic artifact writes and temporary-file lifecycle helpers."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType


class TemporaryPath:
    """Own a temporary file path for an explicit object lifetime."""

    def __init__(self, directory: Path, *, prefix: str = "tmp", suffix: str = "") -> None:
        with tempfile.NamedTemporaryFile(
            prefix=prefix,
            suffix=suffix,
            dir=directory,
            delete=False,
        ) as temporary:
            self.path = Path(temporary.name)
        self._closed = False

    def close(self) -> None:
        """Remove the temporary path once, tolerating prior removal."""
        if self._closed:
            return
        try:
            self.path.unlink(missing_ok=True)
        finally:
            self._closed = True

    def __enter__(self) -> Path:
        return self.path

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()


def _sync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@contextmanager
def temporary_file_path(
    directory: Path,
    *,
    prefix: str = "tmp",
    suffix: str = "",
) -> Iterator[Path]:
    """Yield an adjacent temporary file path and clean it up on exit."""
    temporary = TemporaryPath(directory, prefix=prefix, suffix=suffix)
    try:
        yield temporary.path
    finally:
        temporary.close()


def promote_temporary_file(
    temporary_path: Path,
    final_path: Path,
    *,
    sync_directory: bool = False,
) -> None:
    """Durably replace ``final_path`` with a prepared temporary file."""
    try:
        with temporary_path.open("rb") as temporary:
            os.fsync(temporary.fileno())
        os.replace(temporary_path, final_path)
        if sync_directory:
            _sync_directory(final_path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_bytes(
    path: Path,
    content: bytes,
    *,
    prefix: str = "tmp",
    suffix: str = "",
    sync_directory: bool = False,
) -> None:
    """Replace ``path`` with ``content`` through an adjacent temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary_file_path(path.parent, prefix=prefix, suffix=suffix) as temporary_path:
        with temporary_path.open("wb") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        promote_temporary_file(temporary_path, path, sync_directory=sync_directory)
