import sqlite3
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypedDict

import pyarrow.parquet as pq

from osm_polygon_image_tag.assets.manifest import AssetManifest


def _usable_image_relation_counts(
    manifests: list[tuple[AssetManifest, Path]],
) -> Counter[str]:
    """Count usable image rows by their direct or indirect relation kind."""
    counts: Counter[str] = Counter({"direct_reference": 0, "category_membership": 0})
    for _manifest, output in manifests:
        parquet = pq.ParquetFile(output)
        for batch in parquet.iter_batches(
            columns=["relation_kind", "image_url"],
            batch_size=65_536,
        ):
            relation_kinds = batch.column("relation_kind").to_pylist()
            image_urls = batch.column("image_url").to_pylist()
            for relation_kind, image_url in zip(relation_kinds, image_urls, strict=True):
                if image_url is not None:
                    counts[str(relation_kind)] += 1
    return counts


def asset_statistics(
    catalog_path: Path,
    manifests: list[tuple[AssetManifest, Path]],
    *,
    duplicate_assets: int | None = None,
) -> dict[str, Any]:
    schema_versions, resolver_versions = _manifest_version_counts(manifests)
    image_relation_counts = _usable_image_relation_counts(manifests)
    with sqlite3.connect(catalog_path) as connection:
        provider_counts, status_counts, rows, aggregates = _catalog_aggregates(connection)
        if duplicate_assets is None:
            duplicate_assets = _duplicate_asset_count(connection)
    return _asset_statistics_payload(
        manifests,
        schema_versions,
        resolver_versions,
        provider_counts,
        status_counts,
        rows,
        aggregates,
        duplicate_assets,
        image_relation_counts,
    )


def _asset_statistics_payload(
    manifests: list[tuple[AssetManifest, Path]],
    schema_versions: Counter[int],
    resolver_versions: Counter[int],
    provider_counts: Counter[str],
    status_counts: Counter[str],
    rows: int,
    aggregates: list[int],
    duplicate_assets: int,
    image_relation_counts: Counter[str],
) -> dict[str, Any]:
    values = aggregates
    return {
        "shards": len(manifests),
        "rows": rows,
        "output_bytes": _manifest_output_bytes(manifests),
        "provider_counts": provider_counts,
        "status_counts": status_counts,
        "direct_urls": values[0],
        "stable_direct_urls": max(0, values[0] - values[2]),
        "image_relation_counts": dict(sorted(image_relation_counts.items())),
        "page_urls": values[1],
        "expiring_urls": values[2],
        "licensed_assets": values[3],
        "truncated_categories": values[4],
        "pending_retries": values[5],
        "duplicate_assets": duplicate_assets,
        "cache_hits": _manifest_cache_hits(manifests),
        "network_resolutions": _manifest_network_resolutions(manifests),
        "asset_schema_versions": _stringify_counts(schema_versions),
        "resolver_contract_versions": _stringify_counts(resolver_versions),
    }


def _manifest_output_bytes(manifests: list[tuple[AssetManifest, Path]]) -> int:
    return sum(manifest.output.size_bytes for manifest, _ in manifests)


def _manifest_cache_hits(manifests: list[tuple[AssetManifest, Path]]) -> int:
    return sum(manifest.counts.cache_hits for manifest, _ in manifests)


def _manifest_network_resolutions(manifests: list[tuple[AssetManifest, Path]]) -> int:
    return sum(manifest.counts.resolver_requests for manifest, _ in manifests)


def _stringify_counts(values: Counter[int]) -> dict[str, int]:
    return {str(key): value for key, value in sorted(values.items())}


def _manifest_version_counts(
    manifests: list[tuple[AssetManifest, Path]],
) -> tuple[Counter[int], Counter[int]]:
    schema_versions: Counter[int] = Counter()
    resolver_versions: Counter[int] = Counter()
    for manifest, _output in manifests:
        schema_versions[manifest.asset_schema_version] += 1
        resolver_versions[manifest.resolver_contract_version] += 1
    return schema_versions, resolver_versions


def _catalog_aggregates(
    connection: sqlite3.Connection,
) -> tuple[Counter[str], Counter[str], int, list[int]]:
    provider_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    aggregates = [0] * 6
    rows = 0
    grouped = connection.execute(
        """
        SELECT provider, status, COUNT(*), SUM(image_url IS NOT NULL),
               SUM(page_url IS NOT NULL), SUM(expires_at IS NOT NULL),
               SUM(license_id IS NOT NULL), SUM(category_truncated),
               SUM(retry_after IS NOT NULL)
        FROM asset_observations
        GROUP BY provider, status
        ORDER BY provider, status
        """
    )
    for provider, status, count, *values in grouped:
        provider_counts[str(provider)] += int(count)
        status_counts[str(status)] += int(count)
        rows += int(count)
        for index, value in enumerate(values):
            aggregates[index] += int(value or 0)
    return provider_counts, status_counts, rows, aggregates


def _duplicate_asset_count(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute(
            """
            SELECT COALESCE(SUM(count - 1), 0) FROM (
                SELECT COUNT(*) AS count FROM asset_observations
                GROUP BY provider, canonical_reference, provider_asset_id, image_url
            )
            """
        ).fetchone()[0]
    )


def public_asset_statistics(
    image_path: Path,
    link_path: Path,
    manifests: Iterable[tuple[AssetManifest, Path]],
    *,
    duplicate_images: int = 0,
    duplicate_links: int = 0,
    orphan_rows: int = 0,
) -> dict[str, Any]:
    """Summarize the public one-image and polygon/image tables.

    The private asset catalog describes observations before deduplication. This
    function reads the files that are actually published, so card statistics
    cannot accidentally describe rows that are not in the public release.
    """
    source_manifests = list(manifests)
    schema_versions, resolver_versions = _public_manifest_versions(source_manifests)

    image_stats = _scan_public_images(image_path)
    image_relation_counts, relationship_rows = _scan_public_links(
        link_path, image_stats["usable_image_ids"]
    )

    return {
        "shards": len(source_manifests),
        "rows": image_stats["image_rows"],
        "relationship_rows": relationship_rows,
        "output_bytes": image_path.stat().st_size + link_path.stat().st_size,
        "provider_counts": image_stats["provider_counts"],
        "status_counts": image_stats["status_counts"],
        "direct_urls": image_stats["direct_urls"],
        "stable_direct_urls": image_stats["stable_direct_urls"],
        "image_relation_counts": dict(sorted(image_relation_counts.items())),
        "usable_relationship_rows": sum(image_relation_counts.values()),
        "page_urls": image_stats["page_urls"],
        "expiring_urls": image_stats["expiring_urls"],
        "licensed_assets": image_stats["licensed_assets"],
        "truncated_categories": image_stats["truncated_categories"],
        "pending_retries": image_stats["pending_retries"],
        "duplicate_assets": duplicate_images,
        "duplicate_assets_removed": duplicate_images,
        "duplicate_images_removed": duplicate_images,
        "duplicate_links_removed": duplicate_links,
        "orphan_rows": orphan_rows,
        "cache_hits": sum(manifest.counts.cache_hits for manifest, _ in source_manifests),
        "network_resolutions": sum(
            manifest.counts.resolver_requests for manifest, _ in source_manifests
        ),
        "asset_schema_versions": _stringify_counts(schema_versions),
        "resolver_contract_versions": _stringify_counts(resolver_versions),
    }


def _public_manifest_versions(
    manifests: list[tuple[AssetManifest, Path]],
) -> tuple[Counter[int], Counter[int]]:
    schema_versions: Counter[int] = Counter()
    resolver_versions: Counter[int] = Counter()
    for manifest, _output in manifests:
        schema_versions[manifest.asset_schema_version] += 1
        resolver_versions[manifest.resolver_contract_version] += 1
    return schema_versions, resolver_versions


class _PublicImageStats(TypedDict):
    image_rows: int
    provider_counts: Counter[str]
    status_counts: Counter[str]
    usable_image_ids: set[str]
    direct_urls: int
    stable_direct_urls: int
    page_urls: int
    expiring_urls: int
    licensed_assets: int
    truncated_categories: int
    pending_retries: int


def _scan_public_images(path: Path) -> _PublicImageStats:
    stats: _PublicImageStats = {
        "image_rows": 0,
        "provider_counts": Counter(),
        "status_counts": Counter(),
        "usable_image_ids": set(),
        "direct_urls": 0,
        "stable_direct_urls": 0,
        "page_urls": 0,
        "expiring_urls": 0,
        "licensed_assets": 0,
        "truncated_categories": 0,
        "pending_retries": 0,
    }
    columns = [
        "image_id",
        "provider",
        "status",
        "image_url",
        "image_url_expires_at",
        "page_url",
        "license_id",
        "category_truncated",
        "retry_after",
    ]
    for batch in pq.ParquetFile(path).iter_batches(columns=columns, batch_size=65_536):
        stats["image_rows"] += batch.num_rows
        values = {name: batch.column(name).to_pylist() for name in batch.schema.names}
        for row in zip(*values.values(), strict=True):
            _add_public_image_row(stats, row)
    return stats


def _add_public_image_row(stats: _PublicImageStats, values: tuple[object, ...]) -> None:
    (
        image_id_value,
        provider,
        status,
        image_url,
        expires_at,
        page_url,
        license_id,
        category_truncated,
        retry_after,
    ) = values
    stats["provider_counts"][str(provider)] += 1
    stats["status_counts"][str(status)] += 1
    usable = image_url is not None
    stats["direct_urls"] += int(usable)
    stats["stable_direct_urls"] += int(usable and expires_at is None)
    stats["expiring_urls"] += int(usable and expires_at is not None)
    stats["page_urls"] += int(page_url is not None)
    stats["licensed_assets"] += int(license_id is not None)
    stats["truncated_categories"] += int(bool(category_truncated))
    stats["pending_retries"] += int(retry_after is not None)
    if usable:
        stats["usable_image_ids"].add(str(image_id_value))


def _scan_public_links(path: Path, usable_image_ids: set[str]) -> tuple[Counter[str], int]:
    counts: Counter[str] = Counter({"category_membership": 0, "direct_reference": 0})
    relationship_rows = 0
    for batch in pq.ParquetFile(path).iter_batches(
        columns=["image_id", "relation_kind"], batch_size=65_536
    ):
        relationship_rows += batch.num_rows
        for image_id, relation_kind in zip(
            batch.column("image_id").to_pylist(),
            batch.column("relation_kind").to_pylist(),
            strict=True,
        ):
            if str(image_id) in usable_image_ids:
                counts[str(relation_kind)] += 1
    return counts, relationship_rows
