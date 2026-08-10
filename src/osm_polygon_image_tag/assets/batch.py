"""Bounded reference resolution and accounting for one asset shard."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Mapping
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
    cache: ResolutionCache,
    registry: Registry,
    resolver_contract_version: int,
    cached_records: Mapping[ResolutionKey, ResolutionRecord] | None = None,
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
    cached = cache.get(key) if cached_records is None else cached_records.get(key)
    now = datetime.now(UTC)
    refresh_before = now + timedelta(hours=1)
    expiring = cached is not None and any(
        isinstance(value := asset.get("image_url_expires_at"), str)
        and datetime.fromisoformat(value) <= refresh_before
        for asset in cached.assets
    )
    auth_limited = cached is not None and credential_refresh_required(
        reference.provider,
        cached.status,
        registry.capability,
    )
    if cached is not None and not (
        expiring or auth_limited or retry_refresh_required(cached.status, cached.retry_after, now)
    ):
        return cached, True, True
    record = await registry.resolve_reference(
        reference,
        bbox=polygon_bbox(row),
        resolver_contract_version=resolver_contract_version,
    )
    return record, False, True


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
                cache=self._cache,
                registry=self._registry,
                resolver_contract_version=self._resolver_contract_version,
                cached_records=cached_records,
            )
        return key, record, cache_hit, cacheable

    async def process(
        self,
        pending: list[tuple[Mapping[str, object], SourceReference]],
        sorter: DiskAssetSorter,
    ) -> None:
        if self._stop_requested():
            raise AssetBuildStopped
        unique: dict[ResolutionKey, tuple[Mapping[str, object], SourceReference]] = {}
        for row, reference in pending:
            unique.setdefault(_key(reference, self._resolver_contract_version), (row, reference))

        cacheable_keys = tuple(
            key
            for key, (_row, reference) in unique.items()
            if is_cacheable_canonical_reference(reference.canonical_reference)
        )
        missing_cache_keys = tuple(
            key for key in cacheable_keys if key not in self._snapshot_records
        )
        if missing_cache_keys:
            self._snapshot_records.update(self._cache.get_many(missing_cache_keys))
        cached_records = {
            key: self._snapshot_records[key]
            for key in cacheable_keys
            if key in self._snapshot_records
        }
        resolved = await asyncio.gather(
            *(
                self._resolve_one(key, row, reference, cached_records)
                for key, (row, reference) in unique.items()
            )
        )
        self._cache.put_many(
            tuple(
                record
                for _key, record, cache_hit, cacheable in resolved
                if cacheable and not cache_hit
            )
        )
        records = {key: record for key, record, _cache_hit, _cacheable in resolved}
        self._cache_hits += sum(cache_hit for _key, _record, cache_hit, _cacheable in resolved)
        self._resolver_requests += sum(
            not cache_hit for _key, _record, cache_hit, _cacheable in resolved
        )
        self._snapshot_records.update(
            (key, record) for key, record, _cache_hit, cacheable in resolved if cacheable
        )
        chunk_rows = [
            result_row
            for row, reference in pending
            for result_row in asset_rows(
                row,
                self._polygon_shard,
                reference,
                records[_key(reference, self._resolver_contract_version)],
            )
        ]
        sorter.add(chunk_rows)
        self._statuses.update(str(row["status"]) for row in chunk_rows)
        self._providers.update(str(row["provider"]) for row in chunk_rows)
        self._direct_urls += sum(row["image_url"] is not None for row in chunk_rows)
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
