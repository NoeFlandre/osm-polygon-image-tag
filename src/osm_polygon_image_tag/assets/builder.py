import asyncio
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from osm_polygon_image_tag.assets.build_state import (
    AssetBuildResult,
    asset_paths,
    polygon_identity,
    reusable_manifest,
)
from osm_polygon_image_tag.assets.cache import (
    ResolutionCache,
    ResolutionKey,
    ResolutionRecord,
)
from osm_polygon_image_tag.assets.manifest import (
    ASSET_MANIFEST_SCHEMA_VERSION,
    AssetManifest,
    AssetRunCounts,
    write_asset_manifest,
)
from osm_polygon_image_tag.assets.polygon_input import polygon_bbox, polygon_rows
from osm_polygon_image_tag.assets.references import SourceReference, references_from_row
from osm_polygon_image_tag.assets.rows import asset_rows
from osm_polygon_image_tag.assets.schema import (
    ASSET_SCHEMA_VERSION,
    RESOLVER_CONTRACT_VERSION,
)
from osm_polygon_image_tag.assets.sort import DiskAssetSorter
from osm_polygon_image_tag.assets.storage import AtomicAssetWriter
from osm_polygon_image_tag.core.manifest import Manifest, OutputIdentity, file_sha256
from osm_polygon_image_tag.core.progress import Progress


class Registry(Protocol):
    async def resolve_reference(
        self,
        reference: SourceReference,
        *,
        bbox: tuple[float, float, float, float],
        resolver_contract_version: int,
    ) -> ResolutionRecord: ...


class _BuildStopped(Exception):
    pass


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
) -> tuple[ResolutionRecord, bool]:
    key = _key(reference, resolver_contract_version)
    cached = cache.get(key)
    now = datetime.now(UTC)
    refresh_before = now + timedelta(hours=1)
    expiring = cached is not None and any(
        isinstance(value := asset.get("image_url_expires_at"), str)
        and datetime.fromisoformat(value) <= refresh_before
        for asset in cached.assets
    )
    if cached is not None and not (
        expiring
        or (
            cached.status == "temporary_failure"
            and (cached.retry_after is None or cached.retry_after <= now)
        )
    ):
        return cached, True
    record = await registry.resolve_reference(
        reference,
        bbox=polygon_bbox(row),
        resolver_contract_version=resolver_contract_version,
    )
    cache.put(record)
    return record, False


async def build_asset_shard(
    polygon_manifest: Manifest,
    polygon_path: Path,
    data_root: Path,
    *,
    cache: ResolutionCache,
    registry: Registry,
    stop_requested: Callable[[], bool],
    progress: Progress,
    resolver_contract_version: int = RESOLVER_CONTRACT_VERSION,
) -> AssetBuildResult:
    asset_path, manifest_path = asset_paths(polygon_path, data_root)
    polygon_shard = polygon_path.relative_to(data_root).as_posix()
    source = polygon_identity(polygon_manifest)
    reusable = reusable_manifest(
        manifest_path,
        asset_path,
        source=source,
        data_root=data_root,
        resolver_contract_version=resolver_contract_version,
    )
    if reusable is not None:
        return AssetBuildResult(
            "skipped",
            polygon_shard,
            asset_path,
            manifest_path,
            reusable.counts.rows,
            dict(reusable.counts.statuses),
        )
    if stop_requested():
        return AssetBuildResult("pending", polygon_shard, asset_path, manifest_path, 0, {})

    reference_count = sum(len(references_from_row(row)) for row in polygon_rows(polygon_path))
    semaphore = asyncio.Semaphore(16)
    statuses: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    snapshot_keys: set[ResolutionKey] = set()
    direct_urls = reference_index = cache_hits = resolver_requests = 0

    async def flush(
        pending: list[tuple[Mapping[str, object], SourceReference]],
        sorter: DiskAssetSorter,
    ) -> None:
        nonlocal cache_hits, direct_urls, reference_index, resolver_requests
        if stop_requested():
            raise _BuildStopped
        unique: dict[ResolutionKey, tuple[Mapping[str, object], SourceReference]] = {}
        for row, reference in pending:
            unique.setdefault(_key(reference, resolver_contract_version), (row, reference))

        async def resolve_one(
            key: ResolutionKey,
            row: Mapping[str, object],
            reference: SourceReference,
        ) -> tuple[ResolutionKey, ResolutionRecord, bool]:
            async with semaphore:
                record, cache_hit = await _resolve(
                    reference,
                    row,
                    cache=cache,
                    registry=registry,
                    resolver_contract_version=resolver_contract_version,
                )
            return key, record, cache_hit

        resolved = await asyncio.gather(
            *(resolve_one(key, row, reference) for key, (row, reference) in unique.items())
        )
        records = {key: record for key, record, _cache_hit in resolved}
        cache_hits += sum(cache_hit for _key, _record, cache_hit in resolved)
        resolver_requests += sum(not cache_hit for _key, _record, cache_hit in resolved)
        snapshot_keys.update(records)
        chunk_rows = [
            result_row
            for row, reference in pending
            for result_row in asset_rows(
                row,
                polygon_shard,
                reference,
                records[_key(reference, resolver_contract_version)],
            )
        ]
        sorter.add(chunk_rows)
        statuses.update(str(row["status"]) for row in chunk_rows)
        providers.update(str(row["provider"]) for row in chunk_rows)
        direct_urls += sum(row["image_url"] is not None for row in chunk_rows)
        for _row, _reference in pending:
            reference_index += 1
            progress(
                {
                    "event": "asset_reference_progress",
                    "polygon_shard": polygon_shard,
                    "reference_index": reference_index,
                    "reference_count": reference_count,
                }
            )

    try:
        with DiskAssetSorter(asset_path.parent) as sorter:
            pending: list[tuple[Mapping[str, object], SourceReference]] = []
            for row in polygon_rows(polygon_path):
                for reference in references_from_row(row):
                    pending.append((row, reference))
                    if len(pending) == 128:
                        await flush(pending, sorter)
                        pending.clear()
            if pending:
                await flush(pending, sorter)
            if stop_requested():
                raise _BuildStopped
            with AtomicAssetWriter(asset_path) as writer:
                writer.write(sorter.rows())
            write_result = writer.result
    except _BuildStopped:
        return AssetBuildResult("pending", polygon_shard, asset_path, manifest_path, 0, {})
    if write_result is None:
        raise RuntimeError("asset writer did not finalize")
    snapshot = cache.resolution_snapshot(list(snapshot_keys))
    output = OutputIdentity(
        relative_path=asset_path.relative_to(data_root).as_posix(),
        size_bytes=write_result.size_bytes,
        sha256=file_sha256(asset_path),
        row_count=write_result.row_count,
    )
    counts = AssetRunCounts(
        rows=write_result.row_count,
        statuses=dict(sorted(statuses.items())),
        providers=dict(sorted(providers.items())),
        pending_retries=statuses["temporary_failure"],
        truncated_categories=statuses["category_truncated"],
        direct_urls=direct_urls,
        cache_hits=cache_hits,
        resolver_requests=resolver_requests,
    )
    write_asset_manifest(
        AssetManifest(
            ASSET_MANIFEST_SCHEMA_VERSION,
            ASSET_SCHEMA_VERSION,
            resolver_contract_version,
            source,
            snapshot,
            output,
            counts,
        ),
        manifest_path,
    )
    return AssetBuildResult(
        "built",
        polygon_shard,
        asset_path,
        manifest_path,
        write_result.row_count,
        counts.statuses,
    )
