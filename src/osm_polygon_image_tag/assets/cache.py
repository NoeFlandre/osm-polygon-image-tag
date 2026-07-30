import hashlib
import json
import sqlite3
import threading
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from types import TracebackType

from osm_polygon_image_tag.assets.manifest import ResolutionSnapshotIdentity
from osm_polygon_image_tag.assets.resolution import (
    ResolutionCacheError,
    ResolutionKey,
    ResolutionRecord,
    canonical_json_bytes,
    canonical_record_bytes,
    record_payload,
    validate_resolution_record,
)


class ResolutionCache:
    def __init__(self, path: Path, connection: sqlite3.Connection) -> None:
        self.path = path
        self._connection = connection
        self._lock = threading.RLock()

    @classmethod
    def open(cls, data_root: Path) -> "ResolutionCache":
        cache_dir = data_root / "cache"
        if cache_dir.is_symlink():
            raise ResolutionCacheError("cache directory must not be a symlink")
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / "resolutions.sqlite"
        if path.is_symlink():
            raise ResolutionCacheError("resolution cache must not be a symlink")
        connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS resolutions (
                provider TEXT NOT NULL,
                canonical_reference TEXT NOT NULL,
                resolver_contract_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                response_sha256 TEXT NOT NULL,
                retry_after TEXT,
                PRIMARY KEY (provider, canonical_reference, resolver_contract_version)
            )
            """
        )
        return cls(path, connection)

    def get(self, key: ResolutionKey) -> ResolutionRecord | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT payload_json, response_sha256
                FROM resolutions
                WHERE provider = ? AND canonical_reference = ?
                  AND resolver_contract_version = ?
                """,
                (key.provider, key.canonical_reference, key.resolver_contract_version),
            ).fetchone()
        if row is None:
            return None
        payload_json, stored_sha256 = row
        if hashlib.sha256(payload_json.encode()).hexdigest() != stored_sha256:
            raise ResolutionCacheError("cached resolution digest mismatch")
        payload = json.loads(payload_json)
        retry_value = payload["retry_after"]
        record = ResolutionRecord(
            provider=payload["provider"],
            canonical_reference=payload["canonical_reference"],
            resolver_contract_version=payload["resolver_contract_version"],
            status=payload["status"],
            assets=tuple(dict(asset) for asset in payload["assets"]),
            retry_after=datetime.fromisoformat(retry_value) if retry_value is not None else None,
            reason=payload.get("reason"),
            category_truncated=payload.get("category_truncated", False),
        )
        validate_resolution_record(record)
        return record

    def put(self, record: ResolutionRecord) -> None:
        validate_resolution_record(record)
        payload_json = canonical_record_bytes(record).decode()
        digest = hashlib.sha256(payload_json.encode()).hexdigest()
        retry_after = record.retry_after.isoformat() if record.retry_after is not None else None
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO resolutions (
                        provider, canonical_reference, resolver_contract_version,
                        status, payload_json, response_sha256, retry_after
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, canonical_reference, resolver_contract_version)
                    DO UPDATE SET status=excluded.status,
                                  payload_json=excluded.payload_json,
                                  response_sha256=excluded.response_sha256,
                                  retry_after=excluded.retry_after
                    """,
                    (
                        record.provider,
                        record.canonical_reference,
                        record.resolver_contract_version,
                        record.status,
                        payload_json,
                        digest,
                        retry_after,
                    ),
                )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    def resolution_snapshot(self, keys: Sequence[ResolutionKey]) -> ResolutionSnapshotIdentity:
        entries = []
        for key in sorted(
            set(keys),
            key=lambda item: (
                item.provider,
                item.canonical_reference,
                item.resolver_contract_version,
            ),
        ):
            record = self.get(key)
            if record is None:
                raise ResolutionCacheError("cannot snapshot a missing resolution")
            entries.append(record_payload(record))
        return ResolutionSnapshotIdentity(
            entry_count=len(entries),
            sha256=hashlib.sha256(canonical_json_bytes(entries)).hexdigest(),
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "ResolutionCache":
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()
