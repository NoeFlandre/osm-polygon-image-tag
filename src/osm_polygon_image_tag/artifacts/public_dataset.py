"""Build the deduplicated, publishable view from resumable internal shards."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from osm_polygon_image_tag.artifacts.asset_inventory import verified_asset_manifests
from osm_polygon_image_tag.artifacts.manifest_inventory import verified_manifests
from osm_polygon_image_tag.assets.manifest import AssetManifest as DurableAssetManifest
from osm_polygon_image_tag.assets.manifest import (
    AssetRunCounts,
    AssetSourceIdentity,
    ResolutionSnapshotIdentity,
)
from osm_polygon_image_tag.assets.storage import (
    AssetStorageError,
    validate_asset_parquet,
    write_asset_parquet,
)
from osm_polygon_image_tag.core.atomic import atomic_write_bytes
from osm_polygon_image_tag.core.manifest import (
    DATASET_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    PROCESSING_CONTRACT_VERSION,
    Manifest,
    OutputIdentity,
    RunCounts,
    SourceIdentity,
    file_sha256,
)
from osm_polygon_image_tag.core.schema import dataset_schema

PUBLIC_SCHEMA_VERSION = 1
PUBLIC_POLYGON_RELATIVE = "public/polygons.parquet"
PUBLIC_ASSET_RELATIVE = "public/image_assets.parquet"
PUBLIC_MANIFEST_RELATIVE = "public/public-manifest.json"


@dataclass(frozen=True, slots=True)
class PublicDatasetResult:
    """Publishable deduplicated artifacts and their data-derived counts."""

    polygon_path: Path
    asset_path: Path
    manifest_path: Path
    polygon_manifest: Manifest
    asset_manifest: DurableAssetManifest
    polygon_rows: int
    asset_rows: int
    duplicate_polygon_rows: int
    duplicate_asset_rows: int
    reused: bool = False


def public_polygon_schema() -> pa.Schema:
    """Return the public polygon schema with complete source provenance."""
    fields = list(dataset_schema())
    fields.append(pa.field("source_pbfs", pa.list_(pa.string()), nullable=False))
    metadata = dict(dataset_schema().metadata or {})
    metadata[b"osm_polygon_image_tag_public_schema_version"] = str(PUBLIC_SCHEMA_VERSION).encode()
    return pa.schema(fields, metadata=metadata)


def _identity(row: dict[str, Any]) -> tuple[str, int, int | None]:
    version = row.get("osm_version")
    return (str(row["osm_type"]), int(row["osm_id"]), int(version) if version is not None else None)


def _identity_sort(key: tuple[str, int, int | None]) -> tuple[str, int, int, int]:
    return (key[0], key[1], 0 if key[2] is None else 1, key[2] or 0)


def _jsonable(value: object) -> object:
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _stable_row_key(row: dict[str, Any]) -> str:
    return json.dumps(_jsonable(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iter_rows(
    manifests: Sequence[tuple[Any, Path]], *, batch_size: int = 8192
) -> Iterator[dict[str, Any]]:
    for _manifest, output in manifests:
        for batch in pq.ParquetFile(output).iter_batches(batch_size=batch_size):
            yield from batch.to_pylist()


def _write_polygon_rows(
    rows: Iterable[dict[str, Any]], path: Path, *, batch_size: int = 4096
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = public_polygon_schema()
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    count = 0
    try:
        with pq.ParquetWriter(
            temporary_path, schema, compression="zstd", use_dictionary=True, write_statistics=True
        ) as writer:
            batch: list[dict[str, Any]] = []
            for row in rows:
                batch.append(row)
                if len(batch) == batch_size:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                    count += len(batch)
                    batch.clear()
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                count += len(batch)
        _validate_public_polygon(temporary_path, expected_rows=count)
        with temporary_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return count


def _validate_public_polygon(path: Path, *, expected_rows: int | None = None) -> None:
    parquet = pq.ParquetFile(path)
    actual = parquet.schema_arrow
    expected = public_polygon_schema()
    if (
        actual.names != expected.names
        or actual.metadata != expected.metadata
        or any(
            actual_field.type != expected_field.type
            or actual_field.nullable != expected_field.nullable
            for actual_field, expected_field in zip(actual, expected, strict=True)
        )
    ):
        raise ValueError("public polygon Parquet schema does not match")
    if expected_rows is not None and parquet.metadata.num_rows != expected_rows:
        raise ValueError("public polygon row count does not match")


def validate_public_dataset(data_root: Path) -> dict[str, str]:
    """Validate the materialized public files and return their digests.

    The internal per-PBF shards are deliberately not part of this contract:
    they remain available for resume and audit, while only these two files are
    eligible for the public dataset release.
    """
    root = data_root.resolve()
    manifest_path = root / PUBLIC_MANIFEST_RELATIVE
    polygon_path = root / PUBLIC_POLYGON_RELATIVE
    asset_path = root / PUBLIC_ASSET_RELATIVE
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("public_schema_version") != PUBLIC_SCHEMA_VERSION:
            raise ValueError("unsupported public dataset schema version")
        polygon_output = payload["polygon_output"]
        asset_output = payload["asset_output"]
        if polygon_output["size_bytes"] != polygon_path.stat().st_size:
            raise ValueError("public polygon size mismatch")
        if asset_output["size_bytes"] != asset_path.stat().st_size:
            raise ValueError("public asset size mismatch")
        if file_sha256(polygon_path) != polygon_output["sha256"]:
            raise ValueError("public polygon digest mismatch")
        if file_sha256(asset_path) != asset_output["sha256"]:
            raise ValueError("public asset digest mismatch")
        _validate_public_polygon(polygon_path, expected_rows=int(polygon_output["row_count"]))
        validate_asset_parquet(asset_path, expected_rows=int(asset_output["row_count"]))
    except AssetStorageError as error:
        raise ValueError(f"asset Parquet is invalid: {error}") from error
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(str(error) or "public dataset artifacts are missing or invalid") from error
    return {
        PUBLIC_POLYGON_RELATIVE: str(polygon_output["sha256"]),
        PUBLIC_ASSET_RELATIVE: str(asset_output["sha256"]),
        PUBLIC_MANIFEST_RELATIVE: file_sha256(manifest_path),
    }


def _public_polygon_manifest(path: Path, rows: int) -> Manifest:
    return Manifest(
        MANIFEST_SCHEMA_VERSION,
        PROCESSING_CONTRACT_VERSION,
        DATASET_SCHEMA_VERSION,
        SourceIdentity("internal/polygon-shards", 0, 0, "0" * 64),
        OutputIdentity(PUBLIC_POLYGON_RELATIVE, path.stat().st_size, file_sha256(path), rows),
        "public-dedup",
        RunCounts(rows, {}),
    )


def _public_asset_manifest(path: Path, rows: int) -> DurableAssetManifest:
    return DurableAssetManifest(
        2,
        1,
        1,
        AssetSourceIdentity(PUBLIC_POLYGON_RELATIVE, 0, "0" * 64, rows),
        ResolutionSnapshotIdentity(0, "0" * 64),
        OutputIdentity(PUBLIC_ASSET_RELATIVE, path.stat().st_size, file_sha256(path), rows),
        AssetRunCounts(rows, {}, {}, 0, 0, 0),
    )


def _manifest_payload(
    polygon_manifests: Sequence[tuple[Any, Path]],
    asset_manifests: Sequence[tuple[Any, Path]],
    polygon_manifest: Manifest,
    asset_manifest: DurableAssetManifest,
    *,
    polygon_rows: int,
    asset_rows: int,
    duplicate_polygon_rows: int,
    duplicate_asset_rows: int,
) -> dict[str, Any]:
    return {
        "public_schema_version": PUBLIC_SCHEMA_VERSION,
        "polygon_inputs": [m.output.sha256 for m, _ in polygon_manifests],
        "asset_inputs": [m.output.sha256 for m, _ in asset_manifests],
        "polygon_output": {
            "sha256": polygon_manifest.output.sha256,
            "size_bytes": polygon_manifest.output.size_bytes,
            "row_count": polygon_rows,
        },
        "asset_output": {
            "sha256": asset_manifest.output.sha256,
            "size_bytes": asset_manifest.output.size_bytes,
            "row_count": asset_rows,
        },
        "polygon_rows": polygon_rows,
        "asset_rows": asset_rows,
        "duplicate_polygon_rows": duplicate_polygon_rows,
        "duplicate_asset_rows": duplicate_asset_rows,
    }


def _try_reuse(
    root: Path,
    polygon_manifests: Sequence[tuple[Any, Path]],
    asset_manifests: Sequence[tuple[Any, Path]],
) -> PublicDatasetResult | None:
    manifest_path = root / PUBLIC_MANIFEST_RELATIVE
    polygon_path = root / PUBLIC_POLYGON_RELATIVE
    asset_path = root / PUBLIC_ASSET_RELATIVE
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("public_schema_version") != PUBLIC_SCHEMA_VERSION:
            return None
        if payload.get("polygon_inputs") != [m.output.sha256 for m, _ in polygon_manifests]:
            return None
        if payload.get("asset_inputs") != [m.output.sha256 for m, _ in asset_manifests]:
            return None
        if not polygon_path.is_file() or not asset_path.is_file():
            return None
        polygon_manifest = _public_polygon_manifest(polygon_path, int(payload["polygon_rows"]))
        asset_manifest = _public_asset_manifest(asset_path, int(payload["asset_rows"]))
        if polygon_manifest.output.sha256 != payload["polygon_output"]["sha256"]:
            return None
        if asset_manifest.output.sha256 != payload["asset_output"]["sha256"]:
            return None
        validate_public_dataset(root)
        return PublicDatasetResult(
            polygon_path,
            asset_path,
            manifest_path,
            polygon_manifest,
            asset_manifest,
            polygon_manifest.output.row_count,
            asset_manifest.output.row_count,
            int(payload["duplicate_polygon_rows"]),
            int(payload["duplicate_asset_rows"]),
            reused=True,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def build_public_dataset(
    data_root: Path,
    *,
    manifests: Sequence[tuple[Any, Path]] | None = None,
    asset_manifests: Sequence[tuple[Any, Path]] | None = None,
) -> PublicDatasetResult:
    """Materialize a deterministic deduplicated view without touching inputs."""
    root = data_root.resolve()
    polygon_manifests = list(manifests) if manifests is not None else verified_manifests(root)
    source_assets = (
        list(asset_manifests) if asset_manifests is not None else verified_asset_manifests(root)
    )
    reused = _try_reuse(root, polygon_manifests, source_assets)
    if reused is not None:
        return reused

    selected: dict[tuple[str, int, int | None], tuple[str, dict[str, Any], set[str], str]] = {}
    input_polygon_rows = 0
    for row in _iter_rows(polygon_manifests):
        input_polygon_rows += 1
        key = _identity(row)
        source = str(row["source_pbf"])
        source_feature = str(row.get("source_feature_id") or "")
        candidate = (source, row, {source}, source_feature)
        current = selected.get(key)
        if current is None:
            selected[key] = candidate
            continue
        current[2].add(source)
        if (source, source_feature, _stable_row_key(row)) < (
            current[0],
            current[3],
            _stable_row_key(current[1]),
        ):
            selected[key] = (source, row, current[2], source_feature)

    def polygon_rows() -> Iterator[dict[str, Any]]:
        for key in sorted(selected, key=_identity_sort):
            _source, row, sources, _feature = selected[key]
            output = dict(row)
            output["source_pbfs"] = sorted(sources)
            yield output

    polygon_path = root / PUBLIC_POLYGON_RELATIVE
    polygon_rows_count = _write_polygon_rows(polygon_rows(), polygon_path)
    polygon_manifest = _public_polygon_manifest(polygon_path, polygon_rows_count)
    canonical_sources = {key: value[0] for key, value in selected.items()}
    seen_assets: set[tuple[object, ...]] = set()
    asset_input_rows = 0
    asset_duplicates = 0

    def asset_rows() -> Iterator[dict[str, Any]]:
        nonlocal asset_input_rows, asset_duplicates
        for row in _iter_rows(source_assets):
            asset_input_rows += 1
            key = _identity(row)
            canonical_source = canonical_sources.get(key)
            if canonical_source is None:
                asset_duplicates += 1
                continue
            dedup_key = (
                key,
                row.get("provider"),
                row.get("source_tag_key"),
                row.get("source_tag_value"),
                row.get("canonical_reference"),
                row.get("provider_asset_id"),
                row.get("asset_index"),
                row.get("relation_kind"),
            )
            if dedup_key in seen_assets:
                asset_duplicates += 1
                continue
            seen_assets.add(dedup_key)
            output = dict(row)
            output["source_pbf"] = canonical_source
            output["source_polygon_shard"] = PUBLIC_POLYGON_RELATIVE
            yield output

    asset_path = root / PUBLIC_ASSET_RELATIVE
    asset_rows_count = write_asset_parquet(asset_rows(), asset_path).row_count
    asset_manifest = _public_asset_manifest(asset_path, asset_rows_count)
    payload = _manifest_payload(
        polygon_manifests,
        source_assets,
        polygon_manifest,
        asset_manifest,
        polygon_rows=polygon_rows_count,
        asset_rows=asset_rows_count,
        duplicate_polygon_rows=input_polygon_rows - polygon_rows_count,
        duplicate_asset_rows=asset_duplicates,
    )
    atomic_write_bytes(
        root / PUBLIC_MANIFEST_RELATIVE,
        (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
        prefix=".public-manifest.",
        suffix=".tmp",
        sync_directory=True,
    )
    return PublicDatasetResult(
        polygon_path,
        asset_path,
        root / PUBLIC_MANIFEST_RELATIVE,
        polygon_manifest,
        asset_manifest,
        polygon_rows_count,
        asset_rows_count,
        input_polygon_rows - polygon_rows_count,
        asset_duplicates,
    )


__all__ = [
    "PUBLIC_ASSET_RELATIVE",
    "PUBLIC_MANIFEST_RELATIVE",
    "PUBLIC_POLYGON_RELATIVE",
    "PUBLIC_SCHEMA_VERSION",
    "PublicDatasetResult",
    "build_public_dataset",
    "public_polygon_schema",
    "validate_public_dataset",
]
