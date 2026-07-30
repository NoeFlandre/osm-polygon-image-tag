"""Build the exact allow-listed inventory eligible for publication."""

from pathlib import Path

from osm_polygon_image_tag.artifacts.asset_inventory import verified_asset_manifests
from osm_polygon_image_tag.artifacts.manifest_inventory import verified_manifests
from osm_polygon_image_tag.artifacts.publication_types import PublicationFile
from osm_polygon_image_tag.assets.manifest import (
    AssetManifestError,
    read_asset_manifest,
    read_asset_manifest_header,
)
from osm_polygon_image_tag.assets.schema import (
    ASSET_SCHEMA_VERSION,
    RESOLVER_CONTRACT_VERSION,
)
from osm_polygon_image_tag.assets.storage import AssetStorageError, validate_asset_parquet
from osm_polygon_image_tag.core.errors import PublicationError
from osm_polygon_image_tag.core.manifest import (
    DATASET_SCHEMA_VERSION,
    PROCESSING_CONTRACT_VERSION,
    file_sha256,
    read_manifest,
)


def _reject_symlinks(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            relative = path.relative_to(root).as_posix()
            raise PublicationError(f"symlink in data root: {relative}")


def _regular_file(root: Path, relative: str) -> Path:
    path = root / relative
    if path.is_symlink():
        raise PublicationError(f"publication artifact must not be a symlink: {relative}")
    if not path.is_file():
        raise PublicationError(f"missing publication artifact: {relative}")
    if root.resolve() not in path.resolve().parents:
        raise PublicationError(f"publication artifact escapes data root: {relative}")
    return path


def publication_inventory(data_root: Path) -> tuple[PublicationFile, ...]:
    """Return the deterministic, validated set of files eligible for upload."""
    root = data_root.resolve()
    _reject_symlinks(root)
    manifests = verified_manifests(root)
    asset_manifests = verified_asset_manifests(root)
    for manifest, output in asset_manifests:
        try:
            validate_asset_parquet(output, expected_rows=manifest.output.row_count)
        except AssetStorageError as error:
            raise PublicationError(f"invalid asset Parquet: {output}") from error
        if file_sha256(output) != manifest.output.sha256:
            raise PublicationError(f"asset digest mismatch: {manifest.output.relative_path}")
    manifested_digests = {
        manifest.output.relative_path: manifest.output.sha256 for manifest, _ in manifests
    }
    manifested_digests.update(
        {manifest.output.relative_path: manifest.output.sha256 for manifest, _ in asset_manifests}
    )
    allowed = {"README.md", "statistics/dataset-statistics.json"}
    allowed.update(manifest.output.relative_path for manifest, _ in manifests)
    allowed.update(manifest.output.relative_path for manifest, _ in asset_manifests)
    managed: set[str] = set()
    for path in sorted((root / "manifests").glob("*.manifest.json")):
        manifest = read_manifest(path)
        relative_manifest = path.relative_to(root).as_posix()
        managed.add(relative_manifest)
        output = (root / manifest.output.relative_path).resolve()
        if root not in output.parents:
            raise PublicationError(f"managed output escapes data root: {output}")
        managed.add(output.relative_to(root).as_posix())
        if (
            manifest.processing_contract_version == PROCESSING_CONTRACT_VERSION
            and manifest.dataset_schema_version == DATASET_SCHEMA_VERSION
        ):
            allowed.add(relative_manifest)
    for path in sorted((root / "asset-manifests").glob("*.assets.manifest.json")):
        relative_manifest = path.relative_to(root).as_posix()
        managed.add(relative_manifest)
        try:
            manifest = read_asset_manifest(path)
            output_relative = manifest.output.relative_path
        except AssetManifestError:
            header = read_asset_manifest_header(path, data_root=root)
            output_relative = header.output_relative_path
            manifest = None
        output = (root / output_relative).resolve()
        if root not in output.parents:
            raise PublicationError(f"managed asset output escapes data root: {output}")
        managed.add(output.relative_to(root).as_posix())
        if manifest is not None and (
            manifest.asset_schema_version == ASSET_SCHEMA_VERSION
            and manifest.resolver_contract_version == RESOLVER_CONTRACT_VERSION
        ):
            allowed.add(relative_manifest)
    internal = {
        "catalog/catalog.sqlite",
        "catalog/catalog.sqlite-shm",
        "catalog/catalog.sqlite-wal",
        "receipts/publication.json",
        *(managed - allowed),
    }
    actual: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative != ".DS_Store":
            actual.add(relative)
    private = {relative for relative in actual if relative.startswith("cache/")}
    unexpected = actual - allowed - internal - private
    if unexpected:
        raise PublicationError(f"unexpected data-root entries: {sorted(unexpected)}")
    return tuple(
        PublicationFile(
            local_path=(path := _regular_file(root, relative)),
            remote_path=relative,
            sha256=manifested_digests.get(relative) or file_sha256(path),
            size_bytes=path.stat().st_size,
        )
        for relative in sorted(allowed)
    )
