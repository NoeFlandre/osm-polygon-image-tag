from pathlib import Path

from osm_polygon_image_tag.artifacts.checkpoints import remove_checkpoint_files


def test_remove_checkpoint_files_removes_sqlite_companions(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.sqlite"
    paths = [
        checkpoint,
        Path(f"{checkpoint}-journal"),
        Path(f"{checkpoint}-wal"),
        Path(f"{checkpoint}-shm"),
    ]
    for path in paths:
        path.touch()

    remove_checkpoint_files(checkpoint)

    assert all(not path.exists() for path in paths)
