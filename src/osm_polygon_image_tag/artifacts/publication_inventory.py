"""Build the exact allow-listed inventory eligible for publication."""

from pathlib import Path

from osm_polygon_image_tag.artifacts.hero import HERO_PNG_RELATIVE, packaged_hero_path
from osm_polygon_image_tag.artifacts.public_assets import PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE
from osm_polygon_image_tag.artifacts.public_dataset import (
    PUBLIC_DEDUP_CHECKPOINT_RELATIVE,
    PUBLIC_IMAGE_RELATIVE,
    PUBLIC_LINK_RELATIVE,
    PUBLIC_MANIFEST_RELATIVE,
    PUBLIC_POLYGON_RELATIVE,
    validate_public_dataset,
)
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
from osm_polygon_image_tag.core.errors import PublicationError
from osm_polygon_image_tag.core.manifest import (
    DATASET_SCHEMA_VERSION,
    PROCESSING_CONTRACT_VERSION,
    file_sha256,
    read_manifest,
)
from osm_polygon_image_tag.core.paths import resolve_managed_output


def _reject_symlinks(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            relative = path.relative_to(root).as_posix()
            raise PublicationError(f"symlink in data root: {relative}")


def _resolve_publication_output(root: Path, relative: str, *, label: str) -> Path:
    try:
        return resolve_managed_output(root, relative, label=label)
    except ValueError as error:
        raise PublicationError(str(error)) from error


def _regular_file(root: Path, relative: str) -> Path:
    path = _resolve_publication_output(root, relative, label="publication artifact")
    if not path.is_file():
        raise PublicationError(f"missing publication artifact: {relative}")
    return path


def _validate_png(path: Path, label: str) -> None:
    if not path.is_file():
        raise PublicationError(f"missing {label}: {path}")
    if path.stat().st_size == 0:
        raise PublicationError(f"empty {label}: {path}")
    with path.open("rb") as handle:
        signature = handle.read(8)
    if signature != b"\x89PNG\r\n\x1a\n":
        raise PublicationError(f"invalid {label}: {path}")


def _validated_png_digests(root: Path) -> dict[str, str]:
    from osm_polygon_image_tag.artifacts.geography.render import GEOGRAPHIC_PNG_RELATIVE

    digests: dict[str, str] = {}
    for relative, label in (
        (GEOGRAPHIC_PNG_RELATIVE, "geographic density PNG"),
        (HERO_PNG_RELATIVE, "hero PNG"),
    ):
        png = root / relative
        _validate_png(png, label)
        if png.is_symlink():
            raise PublicationError(f"{label} must not be a symlink: {relative}")
        digests[relative] = file_sha256(png)
    if digests[HERO_PNG_RELATIVE] != file_sha256(packaged_hero_path()):
        raise PublicationError("hero PNG does not match packaged resource")
    return digests


def _managed_polygon_artifacts(root: Path) -> tuple[set[str], set[str]]:
    managed: set[str] = set()
    eligible_manifests: set[str] = set()
    for path in sorted((root / "manifests").glob("*.manifest.json")):
        manifest = read_manifest(path)
        relative_manifest = path.relative_to(root).as_posix()
        managed.add(relative_manifest)
        output = _resolve_publication_output(
            root,
            manifest.output.relative_path,
            label="managed output",
        )
        managed.add(output.relative_to(root).as_posix())
        if (
            manifest.processing_contract_version == PROCESSING_CONTRACT_VERSION
            and manifest.dataset_schema_version == DATASET_SCHEMA_VERSION
        ):
            eligible_manifests.add(relative_manifest)
    return managed, eligible_manifests


def _managed_asset_artifacts(root: Path) -> tuple[set[str], set[str]]:
    managed: set[str] = set()
    eligible_manifests: set[str] = set()
    for path in sorted((root / "asset-manifests").glob("*.assets.manifest.json")):
        relative_manifest = path.relative_to(root).as_posix()
        managed.add(relative_manifest)
        output, eligible = _asset_manifest_artifact(path, root)
        managed.add(output.relative_to(root).as_posix())
        if eligible:
            eligible_manifests.add(relative_manifest)
    return managed, eligible_manifests


def _asset_manifest_artifact(path: Path, root: Path) -> tuple[Path, bool]:
    try:
        manifest = read_asset_manifest(path)
        output_relative = manifest.output.relative_path
    except AssetManifestError:
        header = read_asset_manifest_header(path, data_root=root)
        return (
            _resolve_publication_output(
                root, header.output_relative_path, label="managed asset output"
            ),
            False,
        )
    output = _resolve_publication_output(root, output_relative, label="managed asset output")
    eligible = (
        manifest.asset_schema_version == ASSET_SCHEMA_VERSION
        and manifest.resolver_contract_version == RESOLVER_CONTRACT_VERSION
    )
    return output, eligible


def _actual_files(root: Path) -> set[str]:
    actual: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative != ".DS_Store":
            actual.add(relative)
    return actual


def _publication_files(
    root: Path,
    allowed: set[str],
    manifested_digests: dict[str, str],
) -> tuple[PublicationFile, ...]:
    files: list[PublicationFile] = []
    for relative in sorted(allowed):
        path = _regular_file(root, relative)
        files.append(
            PublicationFile(
                local_path=path,
                remote_path=relative,
                sha256=manifested_digests.get(relative) or file_sha256(path),
                size_bytes=path.stat().st_size,
            )
        )
    return tuple(files)


def publication_inventory(data_root: Path) -> tuple[PublicationFile, ...]:
    """Return the deterministic, validated set of files eligible for upload."""
    root = data_root.resolve()
    _reject_symlinks(root)
    try:
        manifested_digests = validate_public_dataset(root)
    except ValueError as error:
        raise PublicationError(str(error)) from error
    allowed = _allowed_public_files()
    png_digests = _validated_png_digests(root)
    allowed.update(png_digests)
    manifested_digests.update(png_digests)
    polygon_managed, _polygon_eligible = _managed_polygon_artifacts(root)
    asset_managed, _asset_eligible = _managed_asset_artifacts(root)
    managed = polygon_managed | asset_managed
    internal = _internal_files(managed, allowed)
    actual = _actual_files(root)
    private = {relative for relative in actual if relative.startswith("cache/")}
    unexpected = _unexpected_files(actual, allowed, internal, private)
    if unexpected:
        raise PublicationError(f"unexpected data-root entries: {sorted(unexpected)}")
    return _publication_files(root, allowed, manifested_digests)


def _allowed_public_files() -> set[str]:
    return {
        "README.md",
        "citation.cff",
        "statistics/dataset-statistics.json",
        PUBLIC_POLYGON_RELATIVE,
        PUBLIC_IMAGE_RELATIVE,
        PUBLIC_LINK_RELATIVE,
        PUBLIC_MANIFEST_RELATIVE,
    }


def _internal_files(managed: set[str], allowed: set[str]) -> set[str]:
    internal = {
        "catalog/catalog.sqlite",
        "catalog/catalog.sqlite-shm",
        "catalog/catalog.sqlite-wal",
        "catalog/public.sqlite",
        "catalog/public.sqlite-shm",
        "catalog/public.sqlite-wal",
        "receipts/publication.json",
        *(managed - allowed),
    }
    for checkpoint in (PUBLIC_DEDUP_CHECKPOINT_RELATIVE, PUBLIC_ASSET_DEDUP_CHECKPOINT_RELATIVE):
        internal.update(
            {checkpoint, f"{checkpoint}-journal", f"{checkpoint}-wal", f"{checkpoint}-shm"}
        )
    return internal


def _unexpected_files(
    actual: set[str], allowed: set[str], internal: set[str], private: set[str]
) -> set[str]:
    return actual - allowed - internal - private
