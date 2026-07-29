import json
import os
import sqlite3
import tempfile
from pathlib import Path
from types import TracebackType

from osm_polygon_image_tag.extraction import SourceTagRecord


class TagStore:
    def __init__(self, path: Path, connection: sqlite3.Connection, commit_interval: int) -> None:
        self.path = path
        self._connection = connection
        self._commit_interval = commit_interval
        self._pending = 0

    @classmethod
    def create(cls, data_root: Path, *, commit_interval: int = 1000) -> "TagStore":
        if commit_interval <= 0:
            raise ValueError("commit_interval must be positive")
        temporary_root = data_root / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix="tag-store-",
            suffix=".sqlite",
            dir=temporary_root,
        )
        os.close(descriptor)
        path = Path(raw_path)
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            """
            CREATE TABLE tags (
                osm_type TEXT NOT NULL,
                osm_id INTEGER NOT NULL,
                tags_json TEXT NOT NULL,
                PRIMARY KEY (osm_type, osm_id)
            ) WITHOUT ROWID
            """
        )
        connection.commit()
        return cls(path, connection, commit_interval)

    def __enter__(self) -> "TagStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._connection.close()
        for candidate in self.path.parent.glob(f"{self.path.name}*"):
            candidate.unlink(missing_ok=True)

    def add(self, record: SourceTagRecord) -> None:
        encoded = json.dumps(record.tags, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        try:
            self._connection.execute(
                "INSERT INTO tags (osm_type, osm_id, tags_json) VALUES (?, ?, ?)",
                (record.osm_type, record.osm_id, encoded),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError(
                f"duplicate source identity: {record.osm_type}/{record.osm_id}"
            ) from error
        self._pending += 1
        if self._pending >= self._commit_interval:
            self.flush()

    def flush(self) -> None:
        self._connection.commit()
        self._pending = 0

    def lookup(self, osm_type: str, osm_id: int) -> dict[str, str] | None:
        self.flush()
        row = self._connection.execute(
            "SELECT tags_json FROM tags WHERE osm_type = ? AND osm_id = ?",
            (osm_type, osm_id),
        ).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        return dict(value)

    def count(self) -> int:
        self.flush()
        row = self._connection.execute("SELECT COUNT(*) FROM tags").fetchone()
        assert row is not None
        return int(row[0])
