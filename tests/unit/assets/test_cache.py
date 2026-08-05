import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from osm_polygon_image_tag.assets.cache import (
    ResolutionCache,
    ResolutionCacheError,
    ResolutionKey,
    ResolutionRecord,
)
from osm_polygon_image_tag.assets.resolution import record_payload

PANORAMAX_ID = "4492cea4-1018-4285-8074-cf3d37f3c673"


def _record(
    reference: str = PANORAMAX_ID,
    *,
    status: str = "resolved",
    retry_after: datetime | None = None,
) -> ResolutionRecord:
    return ResolutionRecord(
        provider="panoramax",
        canonical_reference=reference,
        resolver_contract_version=1,
        status=status,
        assets=({"image_url": "https://cdn.test/picture.jpg"},),
        retry_after=retry_after,
    )


def test_cache_creates_schema_and_round_trips_canonical_records(tmp_path: Path) -> None:
    with ResolutionCache.open(tmp_path) as cache:
        record = _record()
        cache.put(record)
        loaded = cache.get(record.key)

    assert (tmp_path / "cache" / "resolutions.sqlite").is_file()
    assert loaded == record
    assert loaded is not None
    assert len(loaded.response_sha256) == 64


def test_cache_key_includes_provider_reference_and_contract(tmp_path: Path) -> None:
    with ResolutionCache.open(tmp_path) as cache:
        cache.put(_record())

        assert cache.get(ResolutionKey("panoramax", PANORAMAX_ID, 1)) is not None
        assert cache.get(ResolutionKey("mapillary", PANORAMAX_ID, 1)) is None
        assert cache.get(ResolutionKey("panoramax", "other", 1)) is None
        assert cache.get(ResolutionKey("panoramax", PANORAMAX_ID, 2)) is None


@pytest.mark.parametrize("status", ["not_found", "private", "requires_auth"])
def test_negative_results_are_reused(tmp_path: Path, status: str) -> None:
    with ResolutionCache.open(tmp_path) as cache:
        record = _record(status=status)
        cache.put(record)

        assert cache.get(record.key) == record


def test_temporary_retry_timestamp_round_trips(tmp_path: Path) -> None:
    retry = datetime(2026, 7, 30, 12, 30, tzinfo=UTC)
    with ResolutionCache.open(tmp_path) as cache:
        record = _record(status="temporary_failure", retry_after=retry)
        cache.put(record)

        assert cache.get(record.key) == record


def test_failed_transaction_preserves_previous_record(tmp_path: Path) -> None:
    with ResolutionCache.open(tmp_path) as cache:
        original = _record()
        cache.put(original)
        cache._connection.execute(  # Transactional fault injection.
            """
            CREATE TRIGGER reject_update BEFORE UPDATE ON resolutions
            BEGIN SELECT RAISE(ABORT, 'injected'); END
            """
        )

        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            cache.put(_record(status="private"))

        assert cache.get(original.key) == original


def test_put_many_commits_records_in_one_transaction(tmp_path: Path) -> None:
    records = [_record("first"), _record("second")]
    with ResolutionCache.open(tmp_path) as cache:
        statements: list[str] = []
        cache._connection.set_trace_callback(statements.append)

        cache.put_many(records)

        assert all(cache.get(record.key) == record for record in records)

    assert sum(statement.startswith("BEGIN IMMEDIATE") for statement in statements) == 1
    assert sum(statement == "COMMIT" for statement in statements) == 1


def test_get_many_returns_only_requested_cached_records(tmp_path: Path) -> None:
    records = [_record(str(index)) for index in range(201)]
    missing = ResolutionKey("panoramax", "missing", 1)
    with ResolutionCache.open(tmp_path) as cache:
        cache.put_many(records)

        loaded = cache.get_many([*reversed([record.key for record in records]), missing])

    assert loaded == {record.key: record for record in records}


def test_get_many_rejects_a_corrupt_cached_record(tmp_path: Path) -> None:
    record = _record()
    with ResolutionCache.open(tmp_path) as cache:
        cache.put(record)
        cache._connection.execute(
            "UPDATE resolutions SET payload_json = ? WHERE provider = ?",
            ("{not valid json", record.provider),
        )

        with pytest.raises(ResolutionCacheError, match="digest mismatch"):
            cache.get_many([record.key])


def test_put_many_rolls_back_all_records_on_validation_failure(tmp_path: Path) -> None:
    valid = _record("valid")
    invalid = _record("https://provider.test/item?token=secret")
    with ResolutionCache.open(tmp_path) as cache:
        with pytest.raises(ResolutionCacheError, match="secret-bearing"):
            cache.put_many([valid, invalid])

        assert cache.get(valid.key) is None
        assert cache.get(invalid.key) is None


def test_process_local_writer_lock_serializes_threads(tmp_path: Path) -> None:
    with ResolutionCache.open(tmp_path) as cache:
        records = [_record(str(index)) for index in range(40)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(cache.put, records))

        assert all(cache.get(record.key) == record for record in records)


def test_cache_rejects_symlinked_database_path(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    target = tmp_path / "elsewhere.sqlite"
    target.write_bytes(b"")
    (cache_dir / "resolutions.sqlite").symlink_to(target)

    with pytest.raises(ResolutionCacheError, match="symlink"):
        ResolutionCache.open(tmp_path)


@pytest.mark.parametrize("secret", ["access_token", "api_key", "token", "key"])
def test_cache_rejects_secret_query_parameters(tmp_path: Path, secret: str) -> None:
    with ResolutionCache.open(tmp_path) as cache:
        record = _record(f"https://provider.test/item?{secret}=secret")

        with pytest.raises(ResolutionCacheError, match="secret-bearing"):
            cache.put(record)


def test_cache_rejection_error_never_contains_secret_value(tmp_path: Path) -> None:
    redacted_query_value = "redacted-test-secret-token"
    with ResolutionCache.open(tmp_path) as cache:
        record = _record(f"https://provider.test/item?key={redacted_query_value}")

        with pytest.raises(ResolutionCacheError) as exc_info:
            cache.put(record)

        assert redacted_query_value not in str(exc_info.value)


@pytest.mark.parametrize(
    "corruption",
    ["invalid_json", "missing_status", "invalid_retry_after", "invalid_assets"],
)
def test_cache_wraps_malformed_rows_as_cache_errors(tmp_path: Path, corruption: str) -> None:
    record = _record()
    with ResolutionCache.open(tmp_path) as cache:
        cache.put(record)
        payload = record_payload(record)
        if corruption == "invalid_json":
            payload_json = "{not valid json"
        else:
            if corruption == "missing_status":
                del payload["status"]
            elif corruption == "invalid_retry_after":
                payload["retry_after"] = "not-a-timestamp"
            else:
                payload["assets"] = [None]
            payload_json = json.dumps(payload)
        cache._connection.execute(
            """
            UPDATE resolutions
            SET payload_json = ?, response_sha256 = ?
            WHERE provider = ? AND canonical_reference = ?
              AND resolver_contract_version = ?
            """,
            (
                payload_json,
                hashlib.sha256(payload_json.encode()).hexdigest(),
                record.provider,
                record.canonical_reference,
                record.resolver_contract_version,
            ),
        )

        with pytest.raises(ResolutionCacheError, match="invalid cached resolution"):
            cache.get(record.key)


def test_resolution_snapshot_succeeds_with_no_cacheable_keys(tmp_path: Path) -> None:
    with ResolutionCache.open(tmp_path) as cache:
        snapshot = cache.resolution_snapshot([])

    assert snapshot.entry_count == 0
    assert len(snapshot.sha256) == 64


def test_resolution_snapshot_ignores_unrelated_cache_rows(tmp_path: Path) -> None:
    with ResolutionCache.open(tmp_path) as cache:
        used = _record()
        cache.put(used)
        before = cache.resolution_snapshot([used.key])
        cache.put(_record("unrelated"))

        assert cache.resolution_snapshot([used.key]) == before
        assert before.entry_count == 1
        assert len(before.sha256) == 64


def test_resolution_snapshot_from_records_matches_database_snapshot(tmp_path: Path) -> None:
    with ResolutionCache.open(tmp_path) as cache:
        records = [_record("z-last"), _record("a-first")]
        for record in records:
            cache.put(record)
        keys = [records[0].key, records[1].key, records[0].key]
        database_snapshot = cache.resolution_snapshot(keys)

        supplied_snapshot = cache.resolution_snapshot(
            keys,
            records={
                records[0].key: records[0],
                records[1].key: records[1],
                _record("ignored-extra").key: _record("ignored-extra"),
            },
        )

    assert supplied_snapshot == database_snapshot
    assert supplied_snapshot.entry_count == 2


def test_resolution_snapshot_from_records_avoids_cache_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _record()
    with ResolutionCache.open(tmp_path) as cache:
        get_calls = 0

        def fail_on_get(_key: ResolutionKey) -> ResolutionRecord | None:
            nonlocal get_calls
            get_calls += 1
            raise AssertionError("supplied records must avoid cache reads")

        monkeypatch.setattr(cache, "get", fail_on_get)

        snapshot = cache.resolution_snapshot([record.key], records={record.key: record})

    assert snapshot.entry_count == 1
    assert get_calls == 0


def test_resolution_snapshot_from_records_requires_every_requested_key(tmp_path: Path) -> None:
    record = _record()
    with (
        ResolutionCache.open(tmp_path) as cache,
        pytest.raises(ResolutionCacheError, match="missing resolution"),
    ):
        cache.resolution_snapshot([record.key], records={})


def test_resolution_snapshot_from_records_rejects_key_mismatch(tmp_path: Path) -> None:
    record = _record()
    with (
        ResolutionCache.open(tmp_path) as cache,
        pytest.raises(ResolutionCacheError, match="key"),
    ):
        cache.resolution_snapshot(
            [record.key],
            records={record.key: _record("different")},
        )


@pytest.mark.parametrize(
    "record",
    [
        _record(status="invalid"),
        _record("https://provider.test/item?token=secret"),
    ],
)
def test_resolution_snapshot_from_records_validates_requested_records(
    tmp_path: Path, record: ResolutionRecord
) -> None:
    with ResolutionCache.open(tmp_path) as cache, pytest.raises(ResolutionCacheError):
        cache.resolution_snapshot([record.key], records={record.key: record})


def test_resolution_snapshot_without_records_keeps_database_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = [_record("one"), _record("two")]
    with ResolutionCache.open(tmp_path) as cache:
        for record in records:
            cache.put(record)
        original_get = cache.get
        get_calls = 0

        def count_get(key: ResolutionKey) -> ResolutionRecord | None:
            nonlocal get_calls
            get_calls += 1
            return original_get(key)

        monkeypatch.setattr(cache, "get", count_get)

        snapshot = cache.resolution_snapshot([records[1].key, records[0].key, records[1].key])

    assert snapshot.entry_count == 2
    assert get_calls == 2


def test_cache_preserves_reason_and_category_truncation(tmp_path: Path) -> None:
    record = ResolutionRecord(
        provider="wikimedia_commons",
        canonical_reference="Large",
        resolver_contract_version=1,
        status="category_truncated",
        assets=(),
        retry_after=None,
        reason="category_cap",
        category_truncated=True,
    )
    with ResolutionCache.open(tmp_path) as cache:
        cache.put(record)

        assert cache.get(record.key) == record
