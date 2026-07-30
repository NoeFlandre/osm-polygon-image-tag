import asyncio
from collections import Counter
from collections.abc import Callable, Mapping
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
from osm_polygon_image_tag.assets.storage import write_asset_parquet
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


async def _resolve(
    reference: SourceReference,
    row: Mapping[str, object],
    *,
    cache: ResolutionCache,
    registry: Registry,
    resolver_contract_version: int,
) -> ResolutionRecord:
    key = ResolutionKey(
        reference.provider,
        reference.canonical_reference,
        resolver_contract_version,
    )
    cached = cache.get(key)
    if cached is not None:
        return cached
    record = await registry.resolve_reference(
        reference,
        bbox=polygon_bbox(row),
        resolver_contract_version=resolver_contract_version,
    )
    cache.put(record)
    return record


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

    source_rows = list(polygon_rows(polygon_path))
    references = [(row, reference) for row in source_rows for reference in references_from_row(row)]
    unique: dict[ResolutionKey, tuple[Mapping[str, object], SourceReference]] = {}
    for row, reference in references:
        key = ResolutionKey(
            reference.provider,
            reference.canonical_reference,
            resolver_contract_version,
        )
        unique.setdefault(key, (row, reference))
    if stop_requested():
        return AssetBuildResult("pending", polygon_shard, asset_path, manifest_path, 0, {})
    semaphore = asyncio.Semaphore(16)

    async def resolve_one(
        key: ResolutionKey,
        row: Mapping[str, object],
        reference: SourceReference,
    ) -> tuple[ResolutionKey, ResolutionRecord]:
        async with semaphore:
            record = await _resolve(
                reference,
                row,
                cache=cache,
                registry=registry,
                resolver_contract_version=resolver_contract_version,
            )
        return key, record

    records = dict(
        await asyncio.gather(
            *(resolve_one(key, row, reference) for key, (row, reference) in unique.items())
        )
    )
    for index, (_row, _reference) in enumerate(references, start=1):
        progress(
            {
                "event": "asset_reference_progress",
                "polygon_shard": polygon_shard,
                "reference_index": index,
                "reference_count": len(references),
            }
        )

    output_rows = [
        asset_row
        for row, reference in references
        for asset_row in asset_rows(
            row,
            polygon_shard,
            reference,
            records[
                ResolutionKey(
                    reference.provider,
                    reference.canonical_reference,
                    resolver_contract_version,
                )
            ],
        )
    ]
    output_rows.sort(
        key=lambda row: (
            row["osm_type"],
            row["osm_id"],
            row["provider"],
            row["source_tag_key"],
            row["canonical_reference"],
            row["provider_asset_id"] or "",
            row["asset_index"],
        )
    )
    write_result = write_asset_parquet(output_rows, asset_path)
    statuses = Counter(str(row["status"]) for row in output_rows)
    providers = Counter(str(row["provider"]) for row in output_rows)
    snapshot = cache.resolution_snapshot(list(records))
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
        direct_urls=sum(row["image_url"] is not None for row in output_rows),
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
