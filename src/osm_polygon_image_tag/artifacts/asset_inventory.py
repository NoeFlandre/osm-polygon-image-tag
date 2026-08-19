"""Discover finalized asset manifests that match the current contracts."""

from pathlib import Path

from osm_polygon_image_tag.assets.manifest import (
    ASSET_MANIFEST_SCHEMA_VERSION,
    AssetManifest,
    AssetManifestError,
    AssetManifestHeader,
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
    emit = _progress_callback(progress)
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
        candidate = _verified_asset_candidate(manifest_path, root)
        if candidate is None:
            continue
        manifest, output = candidate
        verified.append((manifest, output))
        pending_retries += manifest.counts.pending_retries
        verified_bytes += manifest.output.size_bytes
        if _should_emit_progress(index, len(manifest_paths)):
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


def _progress_callback(progress: Progress | None) -> Progress:
    return progress if progress is not None else (lambda _event: None)


def _should_emit_progress(index: int, total: int) -> bool:
    return index % 10 == 0 or index == total


def _verified_asset_candidate(manifest_path: Path, root: Path) -> tuple[AssetManifest, Path] | None:
    try:
        manifest = read_asset_manifest(manifest_path)
    except AssetManifestError:
        header = read_asset_manifest_header(manifest_path, data_root=root)
        if not _supported_asset_header(header):
            return None
        raise
    if not _supported_asset_manifest(manifest):
        return None
    output = resolve_managed_output(root, manifest.output.relative_path, label="asset output")
    if output.stat().st_size != manifest.output.size_bytes:
        raise ValueError(f"asset output identity mismatch: {output}")
    return manifest, output


def _supported_asset_header(header: AssetManifestHeader) -> bool:
    return (
        header.manifest_schema_version == ASSET_MANIFEST_SCHEMA_VERSION
        and header.asset_schema_version == ASSET_SCHEMA_VERSION
        and header.resolver_contract_version == RESOLVER_CONTRACT_VERSION
    )


def _supported_asset_manifest(manifest: AssetManifest) -> bool:
    return (
        manifest.asset_schema_version == ASSET_SCHEMA_VERSION
        and manifest.resolver_contract_version == RESOLVER_CONTRACT_VERSION
    )
