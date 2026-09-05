import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from osm_polygon_image_tag.assets.schema import (
    ASSET_SCHEMA_VERSION,
    RESOLVER_CONTRACT_VERSION,
    validate_status,
)
from osm_polygon_image_tag.core.atomic import atomic_write_bytes
from osm_polygon_image_tag.core.errors import ImageTagPipelineError
from osm_polygon_image_tag.core.manifest import OutputIdentity
from osm_polygon_image_tag.core.serialization import canonical_json_bytes

ASSET_MANIFEST_SCHEMA_VERSION = 2


class AssetManifestError(ImageTagPipelineError):
    """Raised when an asset manifest is corrupt or incompatible."""


@dataclass(frozen=True, slots=True)
class AssetSourceIdentity:
    relative_path: str
    size_bytes: int
    sha256: str
    row_count: int


@dataclass(frozen=True, slots=True)
class ResolutionSnapshotIdentity:
    entry_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class AssetRunCounts:
    rows: int
    statuses: dict[str, int]
    providers: dict[str, int]
    pending_retries: int
    truncated_categories: int
    direct_urls: int
    cache_hits: int = 0
    resolver_requests: int = 0


@dataclass(frozen=True, slots=True)
class AssetManifest:
    manifest_schema_version: int
    asset_schema_version: int
    resolver_contract_version: int
    source: AssetSourceIdentity
    resolution_snapshot: ResolutionSnapshotIdentity
    output: OutputIdentity
    counts: AssetRunCounts


@dataclass(frozen=True, slots=True)
class AssetManifestHeader:
    manifest_schema_version: int
    asset_schema_version: int
    resolver_contract_version: int
    output_relative_path: str


def write_asset_manifest(manifest: AssetManifest, path: Path) -> None:
    atomic_write_bytes(
        path,
        canonical_json_bytes(asdict(manifest), newline=True),
        prefix=f".{path.name}.",
        suffix=".tmp",
        sync_directory=True,
    )


def _require_keys(value: Any, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise AssetManifestError(f"invalid {name} fields")
    return value


def _validate_relative_path(relative_path: str, data_root: Path) -> None:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise AssetManifestError("manifest path is outside data root")
    try:
        (data_root / candidate).resolve().relative_to(data_root.resolve())
    except ValueError as error:
        raise AssetManifestError("manifest path is outside data root") from error


def _build_manifest(payload: Any) -> AssetManifest:
    top = _manifest_fields(payload)
    _validate_manifest_versions(top)
    source = _build_source(top["source"])
    snapshot = _build_snapshot(top["resolution_snapshot"])
    output = _build_output(top["output"])
    counts = _build_counts(top["counts"])
    return AssetManifest(
        manifest_schema_version=top["manifest_schema_version"],
        asset_schema_version=top["asset_schema_version"],
        resolver_contract_version=top["resolver_contract_version"],
        source=source,
        resolution_snapshot=snapshot,
        output=output,
        counts=counts,
    )


def _manifest_fields(payload: Any) -> dict[str, Any]:
    return _require_keys(
        payload,
        {
            "manifest_schema_version",
            "asset_schema_version",
            "resolver_contract_version",
            "source",
            "resolution_snapshot",
            "output",
            "counts",
        },
        name="manifest",
    )


def _validate_manifest_versions(top: dict[str, Any]) -> None:
    expected = (
        ("manifest_schema_version", ASSET_MANIFEST_SCHEMA_VERSION, "asset manifest"),
        ("asset_schema_version", ASSET_SCHEMA_VERSION, "asset"),
        ("resolver_contract_version", RESOLVER_CONTRACT_VERSION, "resolver contract"),
    )
    for key, version, label in expected:
        if top[key] != version:
            raise AssetManifestError(f"unsupported {label} schema version")


def _build_source(value: Any) -> AssetSourceIdentity:
    return AssetSourceIdentity(
        **_require_keys(
            value,
            {"relative_path", "size_bytes", "sha256", "row_count"},
            name="source",
        )
    )


def _build_snapshot(value: Any) -> ResolutionSnapshotIdentity:
    return ResolutionSnapshotIdentity(
        **_require_keys(value, {"entry_count", "sha256"}, name="resolution snapshot")
    )


def _build_output(value: Any) -> OutputIdentity:
    return OutputIdentity(
        **_require_keys(
            value,
            {"relative_path", "size_bytes", "sha256", "row_count"},
            name="output",
        )
    )


def _build_counts(value: Any) -> AssetRunCounts:
    counts = AssetRunCounts(
        **_require_keys(
            value,
            {
                "rows",
                "statuses",
                "providers",
                "pending_retries",
                "truncated_categories",
                "direct_urls",
                "cache_hits",
                "resolver_requests",
            },
            name="counts",
        )
    )
    for status in counts.statuses:
        try:
            validate_status(status)
        except ValueError as error:
            raise AssetManifestError(str(error)) from error
    return counts


def read_asset_manifest(path: Path, *, data_root: Path | None = None) -> AssetManifest:
    try:
        manifest = _build_manifest(json.loads(path.read_text(encoding="utf-8")))
        if data_root is not None:
            _validate_relative_path(manifest.source.relative_path, data_root)
            _validate_relative_path(manifest.output.relative_path, data_root)
        return manifest
    except AssetManifestError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise AssetManifestError(f"invalid asset manifest: {path}") from error


def read_asset_manifest_header(
    path: Path,
    *,
    data_root: Path,
) -> AssetManifestHeader:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _parse_manifest_header(payload, data_root)
    except AssetManifestError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise AssetManifestError(f"invalid asset manifest header: {path}") from error


def _parse_manifest_header(payload: Any, data_root: Path) -> AssetManifestHeader:
    if not isinstance(payload, dict) or not isinstance(payload.get("output"), dict):
        raise TypeError
    output = _header_output(payload["output"])
    versions = _header_versions(payload)
    _validate_relative_path(output, data_root)
    return AssetManifestHeader(versions[0], versions[1], versions[2], output)


def _header_output(value: object) -> str:
    if not isinstance(value, dict):
        raise TypeError
    output = value.get("relative_path")
    if not isinstance(output, str):
        raise TypeError
    return output


def _header_versions(payload: dict[str, object]) -> tuple[int, int, int]:
    keys = (
        "manifest_schema_version",
        "asset_schema_version",
        "resolver_contract_version",
    )
    versions = tuple(payload.get(key) for key in keys)
    if not _valid_header_versions(versions):
        raise TypeError
    return cast(tuple[int, int, int], versions)


def _valid_header_versions(versions: tuple[object, ...]) -> bool:
    return all(isinstance(value, int) and not isinstance(value, bool) for value in versions)
