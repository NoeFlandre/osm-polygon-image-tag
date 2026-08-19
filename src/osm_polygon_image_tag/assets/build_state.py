from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pyarrow.parquet as pq

from osm_polygon_image_tag.assets.manifest import (
    AssetManifest,
    AssetManifestError,
    AssetSourceIdentity,
    read_asset_manifest,
)
from osm_polygon_image_tag.assets.refresh_policy import (
    credential_refresh_required,
    retry_refresh_required,
)
from osm_polygon_image_tag.assets.schema import ASSET_SCHEMA_VERSION
from osm_polygon_image_tag.core.manifest import Manifest


@dataclass(frozen=True, slots=True)
class AssetBuildResult:
    status: Literal["built", "skipped", "pending"]
    polygon_shard: str
    asset_path: Path
    manifest_path: Path
    rows: int
    statuses: dict[str, int]


def asset_paths(polygon_path: Path, data_root: Path) -> tuple[Path, Path]:
    stem = polygon_path.name.removesuffix(".parquet")
    return (
        data_root / "assets" / f"{stem}.assets.parquet",
        data_root / "asset-manifests" / f"{stem}.assets.manifest.json",
    )


def polygon_identity(manifest: Manifest) -> AssetSourceIdentity:
    return AssetSourceIdentity(
        relative_path=manifest.output.relative_path,
        size_bytes=manifest.output.size_bytes,
        sha256=manifest.output.sha256,
        row_count=manifest.output.row_count,
    )


def _needs_refresh(
    path: Path,
    capability: Callable[[str], str],
    *,
    check_retry_after: bool = False,
) -> bool:
    now = datetime.now(UTC)
    refresh_before = now + timedelta(hours=1)
    parquet = pq.ParquetFile(path)
    columns = ["provider", "status", "image_url_expires_at"]
    if check_retry_after:
        columns.append("retry_after")
    for batch in parquet.iter_batches(columns=columns):
        if _batch_needs_refresh(
            batch,
            now=now,
            refresh_before=refresh_before,
            capability=capability,
            check_retry_after=check_retry_after,
        ):
            return True
    return False


def _batch_needs_refresh(
    batch: Any,
    *,
    now: datetime,
    refresh_before: datetime,
    capability: Callable[[str], str],
    check_retry_after: bool,
) -> bool:
    providers = batch.column(0).to_pylist()
    statuses = batch.column(1).to_pylist()
    expiries = batch.column(2).to_pylist()
    retries = batch.column(3).to_pylist() if check_retry_after else [None] * len(providers)
    rows = zip(providers, statuses, expiries, retries, strict=True)
    return any(
        _needs_refresh_row(
            provider,
            status,
            expiry,
            retry_after,
            now=now,
            refresh_before=refresh_before,
            capability=capability,
            check_retry_after=check_retry_after,
        )
        for provider, status, expiry, retry_after in rows
    )


def _needs_refresh_row(
    provider: object,
    status: object,
    expiry: object,
    retry_after: object,
    *,
    now: datetime,
    refresh_before: datetime,
    capability: Callable[[str], str],
    check_retry_after: bool,
) -> bool:
    if isinstance(expiry, datetime) and expiry <= refresh_before:
        return True
    if not isinstance(provider, str):
        return False
    if retry_refresh_required(status, retry_after, now, enabled=check_retry_after):
        return True
    return credential_refresh_required(provider, status, capability)


def reusable_manifest(
    path: Path,
    output: Path,
    *,
    source: AssetSourceIdentity,
    data_root: Path,
    resolver_contract_version: int,
    capability: Callable[[str], str],
) -> AssetManifest | None:
    try:
        manifest = read_asset_manifest(path, data_root=data_root)
        if not _manifest_is_reusable(
            manifest,
            output,
            source=source,
            resolver_contract_version=resolver_contract_version,
            capability=capability,
        ):
            return None
        return manifest
    except (AssetManifestError, OSError, ValueError):
        return None


def _manifest_is_reusable(
    manifest: AssetManifest,
    output: Path,
    *,
    source: AssetSourceIdentity,
    resolver_contract_version: int,
    capability: Callable[[str], str],
) -> bool:
    if not _manifest_identity_matches(manifest, source, resolver_contract_version):
        return False
    if not _manifest_output_matches(manifest, output):
        return False
    return not _needs_refresh(
        output,
        capability,
        check_retry_after=manifest.counts.pending_retries > 0,
    )


def _manifest_identity_matches(
    manifest: AssetManifest, source: AssetSourceIdentity, resolver_contract_version: int
) -> bool:
    return (
        manifest.source == source
        and manifest.asset_schema_version == ASSET_SCHEMA_VERSION
        and manifest.resolver_contract_version == resolver_contract_version
    )


def _manifest_output_matches(manifest: AssetManifest, output: Path) -> bool:
    return (
        output.is_file()
        and not output.is_symlink()
        and output.stat().st_size == manifest.output.size_bytes
    )
