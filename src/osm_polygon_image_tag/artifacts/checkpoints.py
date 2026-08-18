"""Small helpers shared by durable SQLite checkpoint stores."""

from pathlib import Path

_SQLITE_CHECKPOINT_SUFFIXES = ("", "-journal", "-wal", "-shm")


def remove_checkpoint_files(path: Path) -> None:
    """Remove a SQLite checkpoint and any companion files, if present."""
    for suffix in _SQLITE_CHECKPOINT_SUFFIXES:
        Path(f"{path}{suffix}").unlink(missing_ok=True)
