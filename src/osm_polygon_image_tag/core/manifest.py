import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from osm_polygon_image_tag.core.atomic import atomic_write_bytes
from osm_polygon_image_tag.core.errors import ImageTagPipelineError
from osm_polygon_image_tag.core.serialization import canonical_json_bytes

MANIFEST_SCHEMA_VERSION = 1
PROCESSING_CONTRACT_VERSION = 2
DATASET_SCHEMA_VERSION = 3


class ManifestError(ImageTagPipelineError):
    """Raised when manifest state is corrupt or incompatible."""


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    relative_path: str
    size_bytes: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True, slots=True)
class OutputIdentity:
    relative_path: str
    size_bytes: int
    sha256: str
    row_count: int


@dataclass(frozen=True, slots=True)
class RunCounts:
    accepted_rows: int
    rejections: dict[str, int]


@dataclass(frozen=True, slots=True)
class Manifest:
    manifest_schema_version: int
    processing_contract_version: int
    dataset_schema_version: int
    source: SourceIdentity
    output: OutputIdentity
    osmium_version: str
    counts: RunCounts


def file_sha256(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        while chunk := file_handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def source_identity(path: Path, *, relative_path: str) -> SourceIdentity:
    before = path.stat()
    digest = file_sha256(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ManifestError(f"source changed while hashing: {path}")
    return SourceIdentity(
        relative_path=relative_path,
        size_bytes=after.st_size,
        mtime_ns=after.st_mtime_ns,
        sha256=digest,
    )


def write_manifest(manifest: Manifest, path: Path) -> None:
    atomic_write_bytes(
        path,
        canonical_json_bytes(asdict(manifest), newline=True),
        prefix=f".{path.name}.",
        suffix=".tmp",
        sync_directory=True,
    )


def _require_keys(value: Any, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ManifestError(f"invalid {name} fields")
    return value


def read_manifest(path: Path) -> Manifest:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        return _build_manifest(payload)
    except ManifestError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise ManifestError(f"invalid manifest: {path}") from error


def _build_manifest(payload: Any) -> Manifest:
    top = _require_keys(
        payload,
        {
            "manifest_schema_version",
            "processing_contract_version",
            "dataset_schema_version",
            "source",
            "output",
            "osmium_version",
            "counts",
        },
        name="manifest",
    )
    if top["manifest_schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ManifestError("unsupported manifest schema version")
    source = SourceIdentity(
        **_require_keys(
            top["source"],
            {"relative_path", "size_bytes", "mtime_ns", "sha256"},
            name="source",
        )
    )
    output = OutputIdentity(
        **_require_keys(
            top["output"],
            {"relative_path", "size_bytes", "sha256", "row_count"},
            name="output",
        )
    )
    counts = RunCounts(
        **_require_keys(top["counts"], {"accepted_rows", "rejections"}, name="counts")
    )
    return Manifest(
        manifest_schema_version=top["manifest_schema_version"],
        processing_contract_version=top["processing_contract_version"],
        dataset_schema_version=top["dataset_schema_version"],
        source=source,
        output=output,
        osmium_version=top["osmium_version"],
        counts=counts,
    )
