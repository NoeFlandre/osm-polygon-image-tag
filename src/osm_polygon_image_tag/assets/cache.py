import hashlib
import json
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, cast

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

_GET_MANY_BATCH_SIZE = 200


def _decode_cached_record(payload_json: object, stored_sha256: object) -> ResolutionRecord:
    if not _valid_cached_values(payload_json, stored_sha256):
        raise ResolutionCacheError("invalid cached resolution")
    payload_text = cast(str, payload_json)
    digest_text = cast(str, stored_sha256)
    if not _digest_matches(payload_text, digest_text):
        raise ResolutionCacheError("cached resolution digest mismatch")
    try:
        return _validated_cached_record(payload_text)
    except ResolutionCacheError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ResolutionCacheError("invalid cached resolution") from error


def _valid_cached_values(payload_json: object, stored_sha256: object) -> bool:
    return isinstance(payload_json, str) and isinstance(stored_sha256, str)


def _digest_matches(payload_json: str, stored_sha256: str) -> bool:
    return hashlib.sha256(payload_json.encode()).hexdigest() == stored_sha256


def _validated_cached_record(payload_json: str) -> ResolutionRecord:
    record = _record_from_payload(json.loads(payload_json))
    validate_resolution_record(record)
    return record


def _record_from_payload(payload: object) -> ResolutionRecord:
    if not isinstance(payload, dict):
        raise TypeError("cached resolution payload must be an object")
    values = cast(Mapping[str, Any], payload)
    retry_value = values["retry_after"]
    return ResolutionRecord(
        provider=values["provider"],
        canonical_reference=values["canonical_reference"],
        resolver_contract_version=values["resolver_contract_version"],
        status=values["status"],
        assets=tuple(dict(asset) for asset in values["assets"]),
        retry_after=_parse_retry_after(retry_value),
        reason=values.get("reason"),
        category_truncated=values.get("category_truncated", False),
        attempt_count=values.get("attempt_count", 1),
    )


def _parse_retry_after(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("retry_after must be an ISO timestamp or null")
    return datetime.fromisoformat(value)


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
        return _decode_cached_record(*row)

    def get_many(self, keys: Sequence[ResolutionKey]) -> dict[ResolutionKey, ResolutionRecord]:
        """Load a bounded batch of cache rows with one query per SQL-safe chunk."""
        requested = tuple(dict.fromkeys(keys))
        if not requested:
            return {}
        requested_tuples = {
            (key.provider, key.canonical_reference, key.resolver_contract_version): key
            for key in requested
        }
        with self._lock:
            rows = _get_many_rows(self._connection, requested)
        return _decode_many_rows(rows, requested_tuples)

    def put(self, record: ResolutionRecord) -> None:
        self.put_many((record,))

    def put_many(self, records: Sequence[ResolutionRecord]) -> None:
        values: list[tuple[object, ...]] = []
        for record in records:
            validate_resolution_record(record)
            payload_json = canonical_record_bytes(record).decode()
            values.append(
                (
                    record.provider,
                    record.canonical_reference,
                    record.resolver_contract_version,
                    record.status,
                    payload_json,
                    hashlib.sha256(payload_json.encode()).hexdigest(),
                    record.retry_after.isoformat() if record.retry_after is not None else None,
                )
            )
        if not values:
            return
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.executemany(
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
                    values,
                )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    def resolution_snapshot(
        self,
        keys: Sequence[ResolutionKey],
        *,
        records: Mapping[ResolutionKey, ResolutionRecord] | None = None,
    ) -> ResolutionSnapshotIdentity:
        entries = []
        for key in _ordered_keys(keys):
            record = _snapshot_record(self, key, records)
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


def _ordered_keys(keys: Sequence[ResolutionKey]) -> list[ResolutionKey]:
    return sorted(
        set(keys),
        key=lambda item: (item.provider, item.canonical_reference, item.resolver_contract_version),
    )


def _snapshot_record(
    cache: ResolutionCache,
    key: ResolutionKey,
    records: Mapping[ResolutionKey, ResolutionRecord] | None,
) -> ResolutionRecord:
    record = cache.get(key) if records is None else records.get(key)
    if record is None:
        raise ResolutionCacheError("cannot snapshot a missing resolution")
    if records is not None:
        if record.key != key:
            raise ResolutionCacheError("resolution record key does not match snapshot key")
        validate_resolution_record(record)
    return record


def _get_many_rows(
    connection: sqlite3.Connection, requested: Sequence[ResolutionKey]
) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for offset in range(0, len(requested), _GET_MANY_BATCH_SIZE):
        rows.extend(_get_many_chunk(connection, requested[offset : offset + _GET_MANY_BATCH_SIZE]))
    return rows


def _get_many_chunk(
    connection: sqlite3.Connection, chunk: Sequence[ResolutionKey]
) -> list[tuple[object, ...]]:
    placeholders = ", ".join("(?, ?, ?)" for _key in chunk)
    parameters = tuple(
        value
        for key in chunk
        for value in (key.provider, key.canonical_reference, key.resolver_contract_version)
    )
    return connection.execute(
        """
        SELECT provider, canonical_reference,
               resolver_contract_version, payload_json, response_sha256
        FROM resolutions
        WHERE (provider, canonical_reference, resolver_contract_version)
              IN (PLACEHOLDERS)
        """.replace("PLACEHOLDERS", placeholders),
        parameters,
    ).fetchall()


def _decode_many_rows(
    rows: Sequence[tuple[object, ...]],
    requested: Mapping[tuple[str, str, int], ResolutionKey],
) -> dict[ResolutionKey, ResolutionRecord]:
    loaded: dict[ResolutionKey, ResolutionRecord] = {}
    for provider, canonical_reference, version, payload_json, stored_sha256 in rows:
        key = requested.get(cast(tuple[str, str, int], (provider, canonical_reference, version)))
        if key is not None:
            loaded[key] = _decode_cached_record(payload_json, stored_sha256)
    return loaded
