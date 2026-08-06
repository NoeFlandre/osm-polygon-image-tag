"""Durable atomic writes for small byte-oriented artifacts."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _sync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


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
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=prefix,
            suffix=suffix,
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        if sync_directory:
            _sync_directory(path.parent)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
