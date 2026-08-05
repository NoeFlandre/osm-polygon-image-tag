from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pyarrow.parquet as pq

from osm_polygon_image_tag.assets.manifest import (
    AssetManifest,
    AssetManifestError,
    AssetSourceIdentity,
    read_asset_manifest,
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
        providers = batch.column(0).to_pylist()
        statuses = batch.column(1).to_pylist()
        expiries = batch.column(2).to_pylist()
        retries = batch.column(3).to_pylist() if check_retry_after else [None] * len(providers)
        rows = zip(providers, statuses, expiries, retries, strict=True)
        for provider, status, expiry, retry_after in rows:
            if isinstance(expiry, datetime) and expiry <= refresh_before:
                return True
            if not isinstance(provider, str):
                continue
            if (
                check_retry_after
                and status == "temporary_failure"
                and (
                    not isinstance(retry_after, datetime)
                    or retry_after.tzinfo is None
                    or retry_after <= now
                )
            ):
                return True
            if status == "requires_auth" and (
                provider == "wikimedia_commons" or capability(provider) == "credentialed"
            ):
                return True
            if (
                status == "resolved_page_only"
                and provider in {"mapillary", "flickr"}
                and capability(provider) == "credentialed"
            ):
                return True
    return False


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
        if (
            manifest.source != source
            or manifest.asset_schema_version != ASSET_SCHEMA_VERSION
            or manifest.resolver_contract_version != resolver_contract_version
            or not output.is_file()
            or output.is_symlink()
            or output.stat().st_size != manifest.output.size_bytes
            or _needs_refresh(
                output,
                capability,
                check_retry_after=manifest.counts.pending_retries > 0,
            )
        ):
            return None
        return manifest
    except (AssetManifestError, OSError, ValueError):
        return None
