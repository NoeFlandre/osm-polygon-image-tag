from collections.abc import Callable, Mapping
from pathlib import Path

from osm_polygon_image_tag.assets.batch import (
    AssetBatchProcessor,
    AssetBuildStopped,
    Registry,
)
from osm_polygon_image_tag.assets.build_state import (
    AssetBuildResult,
    asset_paths,
    polygon_identity,
    reusable_manifest,
)
from osm_polygon_image_tag.assets.cache import ResolutionCache
from osm_polygon_image_tag.assets.manifest import (
    ASSET_MANIFEST_SCHEMA_VERSION,
    AssetManifest,
    write_asset_manifest,
)
from osm_polygon_image_tag.assets.polygon_input import (
    count_polygon_references,
    polygon_rows,
)
from osm_polygon_image_tag.assets.references import SourceReference, references_from_row
from osm_polygon_image_tag.assets.schema import (
    ASSET_SCHEMA_VERSION,
    RESOLVER_CONTRACT_VERSION,
)
from osm_polygon_image_tag.assets.sort import DiskAssetSorter
from osm_polygon_image_tag.assets.storage import AtomicAssetWriter
from osm_polygon_image_tag.core.manifest import Manifest, OutputIdentity, file_sha256
from osm_polygon_image_tag.core.progress import Progress


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
        capability=registry.capability,
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

    reference_count = count_polygon_references(polygon_path)
    processor = AssetBatchProcessor(
        cache=cache,
        registry=registry,
        stop_requested=stop_requested,
        progress=progress,
        polygon_shard=polygon_shard,
        reference_count=reference_count,
        resolver_contract_version=resolver_contract_version,
    )

    try:
        with DiskAssetSorter(asset_path.parent) as sorter:
            pending: list[tuple[Mapping[str, object], SourceReference]] = []
            for row in polygon_rows(polygon_path):
                for reference in references_from_row(row):
                    pending.append((row, reference))
                    if len(pending) == 128:
                        await processor.process(pending, sorter)
                        pending.clear()
            if pending:
                await processor.process(pending, sorter)
            if stop_requested():
                raise AssetBuildStopped
            with AtomicAssetWriter(asset_path) as writer:
                writer.write(sorter.rows())
            write_result = writer.result
    except AssetBuildStopped:
        return AssetBuildResult("pending", polygon_shard, asset_path, manifest_path, 0, {})
    if write_result is None:
        raise RuntimeError("asset writer did not finalize")
    snapshot = processor.resolution_snapshot()
    output = OutputIdentity(
        relative_path=asset_path.relative_to(data_root).as_posix(),
        size_bytes=write_result.size_bytes,
        sha256=file_sha256(asset_path),
        row_count=write_result.row_count,
    )
    counts = processor.counts(write_result.row_count)
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
