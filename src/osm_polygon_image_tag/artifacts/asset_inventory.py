"""Discover finalized asset manifests that match the current contracts."""

from pathlib import Path

from osm_polygon_image_tag.assets.manifest import (
    ASSET_MANIFEST_SCHEMA_VERSION,
    AssetManifest,
    AssetManifestError,
    read_asset_manifest,
    read_asset_manifest_header,
)
from osm_polygon_image_tag.assets.schema import (
    ASSET_SCHEMA_VERSION,
    RESOLVER_CONTRACT_VERSION,
)
from osm_polygon_image_tag.core.paths import resolve_managed_output
from osm_polygon_image_tag.core.progress import Progress


def verified_asset_manifests(
    data_root: Path, *, progress: Progress | None = None
) -> list[tuple[AssetManifest, Path]]:
    emit = progress or (lambda _event: None)
    root = data_root.resolve()
    manifest_paths = sorted((root / "asset-manifests").glob("*.assets.manifest.json"))
    emit(
        {
            "event": "metadata_asset_manifest_scan_started",
            "manifest_count": len(manifest_paths),
        }
    )
    verified: list[tuple[AssetManifest, Path]] = []
    pending_retries = 0
    verified_bytes = 0
    for index, manifest_path in enumerate(manifest_paths, start=1):
        try:
            manifest = read_asset_manifest(manifest_path)
        except AssetManifestError:
            header = read_asset_manifest_header(manifest_path, data_root=root)
            if (
                header.manifest_schema_version != ASSET_MANIFEST_SCHEMA_VERSION
                or header.asset_schema_version != ASSET_SCHEMA_VERSION
                or header.resolver_contract_version != RESOLVER_CONTRACT_VERSION
            ):
                continue
            raise
        if (
            manifest.asset_schema_version != ASSET_SCHEMA_VERSION
            or manifest.resolver_contract_version != RESOLVER_CONTRACT_VERSION
        ):
            continue
        output = resolve_managed_output(
            root,
            manifest.output.relative_path,
            label="asset output",
        )
        if output.stat().st_size != manifest.output.size_bytes:
            raise ValueError(f"asset output identity mismatch: {output}")
        verified.append((manifest, output))
        pending_retries += manifest.counts.pending_retries
        verified_bytes += manifest.output.size_bytes
        if index % 10 == 0 or index == len(manifest_paths):
            emit(
                {
                    "event": "metadata_asset_manifest_scan_progress",
                    "manifest_count": len(manifest_paths),
                    "manifest_index": index,
                    "verified_shards": len(verified),
                    "pending_retries": pending_retries,
                    "verified_bytes": verified_bytes,
                }
            )
    emit(
        {
            "event": "metadata_asset_manifest_scan_completed",
            "manifest_count": len(manifest_paths),
            "verified_shards": len(verified),
            "pending_retries": pending_retries,
            "verified_bytes": verified_bytes,
        }
    )
    return verified
