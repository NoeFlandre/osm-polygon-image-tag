"""Discover finalized shard manifests that match the current data contract."""

from pathlib import Path

from osm_polygon_image_tag.core.manifest import (
    DATASET_SCHEMA_VERSION,
    PROCESSING_CONTRACT_VERSION,
    Manifest,
    read_manifest,
)
from osm_polygon_image_tag.core.paths import resolve_managed_output
from osm_polygon_image_tag.core.progress import Progress


def verified_manifests(
    data_root: Path, *, progress: Progress | None = None
) -> list[tuple[Manifest, Path]]:
    """Return current-contract manifests whose output size and location are valid."""
    emit = progress or (lambda _event: None)
    manifest_paths = sorted((data_root / "manifests").glob("*.manifest.json"))
    emit({"event": "metadata_manifest_scan_started", "manifest_count": len(manifest_paths)})
    verified: list[tuple[Manifest, Path]] = []
    skipped = 0
    verified_bytes = 0
    for index, manifest_path in enumerate(manifest_paths, start=1):
        manifest = read_manifest(manifest_path)
        compatible = (
            manifest.processing_contract_version == PROCESSING_CONTRACT_VERSION
            and manifest.dataset_schema_version == DATASET_SCHEMA_VERSION
        )
        if compatible:
            output = resolve_managed_output(
                data_root,
                manifest.output.relative_path,
                label="output",
            )
            if output.stat().st_size != manifest.output.size_bytes:
                raise ValueError(f"output identity mismatch: {output}")
            verified.append((manifest, output))
            verified_bytes += manifest.output.size_bytes
        else:
            skipped += 1
        if index % 10 == 0 or index == len(manifest_paths):
            emit(
                {
                    "event": "metadata_manifest_scan_progress",
                    "manifest_count": len(manifest_paths),
                    "manifest_index": index,
                    "verified_shards": len(verified),
                    "skipped_incompatible": skipped,
                    "verified_bytes": verified_bytes,
                }
            )
    emit(
        {
            "event": "metadata_manifest_scan_completed",
            "manifest_count": len(manifest_paths),
            "verified_shards": len(verified),
            "skipped_incompatible": skipped,
            "verified_bytes": verified_bytes,
        }
    )
    return verified
