import pickle
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from types import TracebackType


class DiskAssetSorter:
    """Bounded-memory global ordering for one asset shard."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=".asset-sort.",
            suffix=".sqlite",
            dir=directory,
            delete=False,
        ) as temporary:
            self._path = Path(temporary.name)
        try:
            self._connection = sqlite3.connect(self._path)
            self._connection.execute("PRAGMA cache_size=-2048")
            self._connection.execute("PRAGMA temp_store=FILE")
            self._connection.execute(
                """
                CREATE TABLE rows (
                    osm_type TEXT NOT NULL,
                    osm_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    source_tag_key TEXT NOT NULL,
                    canonical_reference TEXT NOT NULL,
                    provider_asset_id TEXT NOT NULL,
                    asset_index INTEGER NOT NULL,
                    payload BLOB NOT NULL
                )
                """
            )
        except BaseException:
            self._path.unlink(missing_ok=True)
            raise

    def add(self, rows: Iterable[Mapping[str, object]]) -> None:
        self._connection.executemany(
            "INSERT INTO rows VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    row["osm_type"],
                    row["osm_id"],
                    row["provider"],
                    row["source_tag_key"],
                    row["canonical_reference"],
                    row["provider_asset_id"] or "",
                    row["asset_index"],
                    sqlite3.Binary(pickle.dumps(dict(row), protocol=5)),
                )
                for row in rows
            ),
        )

    def rows(self) -> Iterator[dict[str, object]]:
        self._connection.commit()
        cursor = self._connection.execute(
            """
            SELECT payload FROM rows
            ORDER BY osm_type, osm_id, provider, source_tag_key,
                     canonical_reference, provider_asset_id, asset_index
            """
        )
        for (payload,) in cursor:
            row = pickle.loads(payload)  # noqa: S301
            if not isinstance(row, dict):
                raise TypeError("invalid internal asset sort row")
            yield row

    def close(self) -> None:
        self._connection.close()
        self._path.unlink(missing_ok=True)

    def __enter__(self) -> "DiskAssetSorter":
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()
