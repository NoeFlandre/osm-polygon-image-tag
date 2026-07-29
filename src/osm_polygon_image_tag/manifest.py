import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from osm_polygon_image_tag.errors import ImageTagPipelineError

MANIFEST_SCHEMA_VERSION = 1
PROCESSING_CONTRACT_VERSION = 1
DATASET_SCHEMA_VERSION = 1


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


def _canonical_bytes(manifest: Manifest) -> bytes:
    return (
        json.dumps(
            asdict(manifest),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def write_manifest(manifest: Manifest, path: Path) -> None:
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
        raise ManifestError(f"invalid {name} fields")
    return value


def read_manifest(path: Path) -> Manifest:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
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
        counts_value = _require_keys(
            top["counts"],
            {"accepted_rows", "rejections"},
            name="counts",
        )
        counts = RunCounts(**counts_value)
        return Manifest(
            manifest_schema_version=top["manifest_schema_version"],
            processing_contract_version=top["processing_contract_version"],
            dataset_schema_version=top["dataset_schema_version"],
            source=source,
            output=output,
            osmium_version=top["osmium_version"],
            counts=counts,
        )
    except ManifestError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise ManifestError(f"invalid manifest: {path}") from error
