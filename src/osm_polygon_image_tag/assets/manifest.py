import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from osm_polygon_image_tag.assets.schema import (
    ASSET_SCHEMA_VERSION,
    RESOLVER_CONTRACT_VERSION,
    validate_status,
)
from osm_polygon_image_tag.core.errors import ImageTagPipelineError
from osm_polygon_image_tag.core.manifest import OutputIdentity

ASSET_MANIFEST_SCHEMA_VERSION = 1


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


def _canonical_bytes(manifest: AssetManifest) -> bytes:
    payload = json.dumps(
        asdict(manifest),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{payload}\n".encode()


def write_asset_manifest(manifest: AssetManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
        temporary.write(_canonical_bytes(manifest))
        temporary.flush()
        os.fsync(temporary.fileno())
    try:
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


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
    top = _require_keys(
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
    if top["manifest_schema_version"] != ASSET_MANIFEST_SCHEMA_VERSION:
        raise AssetManifestError("unsupported asset manifest schema version")
    if top["asset_schema_version"] != ASSET_SCHEMA_VERSION:
        raise AssetManifestError("unsupported asset schema version")
    if top["resolver_contract_version"] != RESOLVER_CONTRACT_VERSION:
        raise AssetManifestError("unsupported resolver contract version")
    source = AssetSourceIdentity(
        **_require_keys(
            top["source"],
            {"relative_path", "size_bytes", "sha256", "row_count"},
            name="source",
        )
    )
    snapshot = ResolutionSnapshotIdentity(
        **_require_keys(
            top["resolution_snapshot"],
            {"entry_count", "sha256"},
            name="resolution snapshot",
        )
    )
    output = OutputIdentity(
        **_require_keys(
            top["output"],
            {"relative_path", "size_bytes", "sha256", "row_count"},
            name="output",
        )
    )
    counts = AssetRunCounts(
        **_require_keys(
            top["counts"],
            {
                "rows",
                "statuses",
                "providers",
                "pending_retries",
                "truncated_categories",
                "direct_urls",
            },
            name="counts",
        )
    )
    for status in counts.statuses:
        try:
            validate_status(status)
        except ValueError as error:
            raise AssetManifestError(str(error)) from error
    return AssetManifest(
        manifest_schema_version=top["manifest_schema_version"],
        asset_schema_version=top["asset_schema_version"],
        resolver_contract_version=top["resolver_contract_version"],
        source=source,
        resolution_snapshot=snapshot,
        output=output,
        counts=counts,
    )


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
        if not isinstance(payload, dict) or not isinstance(payload.get("output"), dict):
            raise TypeError
        output = payload["output"]["relative_path"]
        versions = (
            payload["manifest_schema_version"],
            payload["asset_schema_version"],
            payload["resolver_contract_version"],
        )
        if not isinstance(output, str) or not all(isinstance(value, int) for value in versions):
            raise TypeError
        _validate_relative_path(output, data_root)
        return AssetManifestHeader(*versions, output)
    except AssetManifestError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise AssetManifestError(f"invalid asset manifest header: {path}") from error
