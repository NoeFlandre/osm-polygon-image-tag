"""Bounded reference resolution and accounting for one asset shard."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from osm_polygon_image_tag.assets.cache import (
    ResolutionCache,
    ResolutionKey,
    ResolutionRecord,
)
from osm_polygon_image_tag.assets.manifest import (
    AssetRunCounts,
    ResolutionSnapshotIdentity,
)
from osm_polygon_image_tag.assets.polygon_input import polygon_bbox
from osm_polygon_image_tag.assets.references import SourceReference
from osm_polygon_image_tag.assets.refresh_policy import (
    credential_refresh_required,
    retry_refresh_required,
)
from osm_polygon_image_tag.assets.resolution import is_cacheable_canonical_reference
from osm_polygon_image_tag.assets.rows import asset_rows
from osm_polygon_image_tag.assets.sort import DiskAssetSorter
from osm_polygon_image_tag.core.progress import Progress


class Registry(Protocol):
    def capability(self, provider: str) -> str: ...

    async def resolve_reference(
        self,
        reference: SourceReference,
        *,
        bbox: tuple[float, float, float, float],
        resolver_contract_version: int,
    ) -> ResolutionRecord: ...


class AssetBuildStopped(Exception):
    """Signal a requested stop before a shard can be finalized."""


def _key(reference: SourceReference, version: int) -> ResolutionKey:
    return ResolutionKey(
        reference.provider,
        reference.canonical_reference,
        version,
    )


async def _resolve(
    reference: SourceReference,
    row: Mapping[str, object],
    *,
    registry: Registry,
    resolver_contract_version: int,
    cached_records: Mapping[ResolutionKey, ResolutionRecord],
) -> tuple[ResolutionRecord, bool, bool]:
    key = _key(reference, resolver_contract_version)
    if not is_cacheable_canonical_reference(reference.canonical_reference):
        # Secret-like source references are resolved once using the original
        # request URL but are never written to the durable cache or recorded in
        # the resolution snapshot, so they cannot abort the shard.
        record = await registry.resolve_reference(
            reference,
            bbox=polygon_bbox(row),
            resolver_contract_version=resolver_contract_version,
        )
        return record, False, False
    cached = cached_records.get(key)
    if cached is not None and _cached_is_reusable(cached, reference, registry.capability):
        return cached, True, True
    record = await registry.resolve_reference(
        reference,
        bbox=polygon_bbox(row),
        resolver_contract_version=resolver_contract_version,
    )
    return record, False, True


def _cached_is_reusable(
    cached: ResolutionRecord | None,
    reference: SourceReference,
    capability: Callable[[str], str],
) -> bool:
    if cached is None:
        return False
    now = datetime.now(UTC)
    return not _cached_refresh_required(cached, reference, capability, now)


def _cached_refresh_required(
    cached: ResolutionRecord,
    reference: SourceReference,
    capability: Callable[[str], str],
    now: datetime,
) -> bool:
    return (
        _cached_asset_expiring(cached, now)
        or credential_refresh_required(reference.provider, cached.status, capability)
        or retry_refresh_required(cached.status, cached.retry_after, now)
    )


def _cached_asset_expiring(cached: ResolutionRecord, now: datetime) -> bool:
    refresh_before = now + timedelta(hours=1)
    for asset in cached.assets:
        value = asset.get("image_url_expires_at")
        if isinstance(value, str) and datetime.fromisoformat(value) <= refresh_before:
            return True
    return False


class AssetBatchProcessor:
    """Resolve bounded reference batches and retain shard-level accounting."""

    def __init__(
        self,
        *,
        cache: ResolutionCache,
        registry: Registry,
        stop_requested: Callable[[], bool],
        progress: Progress,
        polygon_shard: str,
        reference_count: int,
        resolver_contract_version: int,
    ) -> None:
        self._cache = cache
        self._registry = registry
        self._stop_requested = stop_requested
        self._progress = progress
        self._polygon_shard = polygon_shard
        self._reference_count = reference_count
        self._resolver_contract_version = resolver_contract_version
        self._semaphore = asyncio.Semaphore(16)
        self._statuses: Counter[str] = Counter()
        self._providers: Counter[str] = Counter()
        self._snapshot_records: dict[ResolutionKey, ResolutionRecord] = {}
        self._direct_urls = 0
        self._reference_index = 0
        self._cache_hits = 0
        self._resolver_requests = 0

    async def _resolve_one(
        self,
        key: ResolutionKey,
        row: Mapping[str, object],
        reference: SourceReference,
        cached_records: Mapping[ResolutionKey, ResolutionRecord],
    ) -> tuple[ResolutionKey, ResolutionRecord, bool, bool]:
        async with self._semaphore:
            record, cache_hit, cacheable = await _resolve(
                reference,
                row,
                registry=self._registry,
                resolver_contract_version=self._resolver_contract_version,
                cached_records=cached_records,
            )
        return key, record, cache_hit, cacheable

    def _unique_pending(
        self, pending: list[tuple[Mapping[str, object], SourceReference]]
    ) -> dict[ResolutionKey, tuple[Mapping[str, object], SourceReference]]:
        unique: dict[ResolutionKey, tuple[Mapping[str, object], SourceReference]] = {}
        for row, reference in pending:
            unique.setdefault(_key(reference, self._resolver_contract_version), (row, reference))
        return unique

    def _cached_records(
        self,
        unique: Mapping[ResolutionKey, tuple[Mapping[str, object], SourceReference]],
    ) -> Mapping[ResolutionKey, ResolutionRecord]:
        cacheable_keys = _cacheable_keys(unique)
        missing_keys = _missing_snapshot_keys(cacheable_keys, self._snapshot_records)
        if missing_keys:
            self._snapshot_records.update(self._cache.get_many(missing_keys))
        return _cached_snapshot_values(cacheable_keys, self._snapshot_records)

    async def _resolve_unique(
        self,
        unique: Mapping[ResolutionKey, tuple[Mapping[str, object], SourceReference]],
        cached_records: Mapping[ResolutionKey, ResolutionRecord],
    ) -> list[tuple[ResolutionKey, ResolutionRecord, bool, bool]]:
        return list(
            await asyncio.gather(
                *(
                    self._resolve_one(key, row, reference, cached_records)
                    for key, (row, reference) in unique.items()
                )
            )
        )

    def _record_resolutions(
        self, resolved: list[tuple[ResolutionKey, ResolutionRecord, bool, bool]]
    ) -> dict[ResolutionKey, ResolutionRecord]:
        self._cache.put_many(_new_cache_records(resolved))
        self._cache_hits += sum(cache_hit for _key, _record, cache_hit, _cacheable in resolved)
        self._resolver_requests += sum(
            not cache_hit for _key, _record, cache_hit, _cacheable in resolved
        )
        records = _resolution_records(resolved)
        self._snapshot_records.update(_cacheable_records(resolved))
        return records

    def _append_rows(
        self,
        pending: list[tuple[Mapping[str, object], SourceReference]],
        records: Mapping[ResolutionKey, ResolutionRecord],
        sorter: DiskAssetSorter,
    ) -> None:
        chunk_rows = _asset_rows_for_pending(
            pending, records, self._polygon_shard, self._resolver_contract_version
        )
        sorter.add(chunk_rows)
        self._record_row_counts(chunk_rows)

    def _record_row_counts(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._statuses.update(str(row["status"]) for row in rows)
        self._providers.update(str(row["provider"]) for row in rows)
        self._direct_urls += sum(row["image_url"] is not None for row in rows)

    def _emit_progress(self, pending: list[tuple[Mapping[str, object], SourceReference]]) -> None:
        for _row, _reference in pending:
            self._reference_index += 1
            self._progress(
                {
                    "event": "asset_reference_progress",
                    "polygon_shard": self._polygon_shard,
                    "reference_index": self._reference_index,
                    "reference_count": self._reference_count,
                }
            )

    async def process(
        self,
        pending: list[tuple[Mapping[str, object], SourceReference]],
        sorter: DiskAssetSorter,
    ) -> None:
        if self._stop_requested():
            raise AssetBuildStopped
        unique = self._unique_pending(pending)
        cached_records = self._cached_records(unique)
        resolved = await self._resolve_unique(unique, cached_records)
        records = self._record_resolutions(resolved)
        self._append_rows(pending, records, sorter)
        self._emit_progress(pending)

    def resolution_snapshot(self) -> ResolutionSnapshotIdentity:
        return self._cache.resolution_snapshot(
            list(self._snapshot_records),
            records=self._snapshot_records,
        )

    def counts(self, row_count: int) -> AssetRunCounts:
        return AssetRunCounts(
            rows=row_count,
            statuses=dict(sorted(self._statuses.items())),
            providers=dict(sorted(self._providers.items())),
            pending_retries=self._statuses["temporary_failure"],
            truncated_categories=self._statuses["category_truncated"],
            direct_urls=self._direct_urls,
            cache_hits=self._cache_hits,
            resolver_requests=self._resolver_requests,
        )


def _cacheable_keys(
    unique: Mapping[ResolutionKey, tuple[Mapping[str, object], SourceReference]],
) -> tuple[ResolutionKey, ...]:
    return tuple(
        key
        for key, (_row, reference) in unique.items()
        if is_cacheable_canonical_reference(reference.canonical_reference)
    )


def _missing_snapshot_keys(
    keys: tuple[ResolutionKey, ...], snapshots: Mapping[ResolutionKey, ResolutionRecord]
) -> tuple[ResolutionKey, ...]:
    return tuple(key for key in keys if key not in snapshots)


def _cached_snapshot_values(
    keys: tuple[ResolutionKey, ...], snapshots: Mapping[ResolutionKey, ResolutionRecord]
) -> dict[ResolutionKey, ResolutionRecord]:
    return {key: snapshots[key] for key in keys if key in snapshots}


def _new_cache_records(
    resolved: list[tuple[ResolutionKey, ResolutionRecord, bool, bool]],
) -> tuple[ResolutionRecord, ...]:
    return tuple(
        record for _key, record, cache_hit, cacheable in resolved if cacheable and not cache_hit
    )


def _resolution_records(
    resolved: list[tuple[ResolutionKey, ResolutionRecord, bool, bool]],
) -> dict[ResolutionKey, ResolutionRecord]:
    return {key: record for key, record, _cache_hit, _cacheable in resolved}


def _cacheable_records(
    resolved: list[tuple[ResolutionKey, ResolutionRecord, bool, bool]],
) -> dict[ResolutionKey, ResolutionRecord]:
    return {key: record for key, record, _cache_hit, cacheable in resolved if cacheable}


def _asset_rows_for_pending(
    pending: list[tuple[Mapping[str, object], SourceReference]],
    records: Mapping[ResolutionKey, ResolutionRecord],
    polygon_shard: str,
    resolver_contract_version: int,
) -> list[dict[str, object]]:
    return [
        result_row
        for row, reference in pending
        for result_row in asset_rows(
            row,
            polygon_shard,
            reference,
            records[_key(reference, resolver_contract_version)],
        )
    ]
