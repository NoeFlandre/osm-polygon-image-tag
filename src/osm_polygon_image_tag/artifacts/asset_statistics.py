import sqlite3
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

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
    schema_versions: Counter[int] = Counter()
    resolver_versions: Counter[int] = Counter()
    for manifest, _output in manifests:
        schema_versions[manifest.asset_schema_version] += 1
        resolver_versions[manifest.resolver_contract_version] += 1
    image_relation_counts = _usable_image_relation_counts(manifests)
    with sqlite3.connect(catalog_path) as connection:
        provider_counts: Counter[str] = Counter()
        status_counts: Counter[str] = Counter()
        aggregates = [0] * 6
        rows = 0
        grouped = connection.execute(
            """
            SELECT
                provider,
                status,
                COUNT(*),
                SUM(image_url IS NOT NULL),
                SUM(page_url IS NOT NULL),
                SUM(expires_at IS NOT NULL),
                SUM(license_id IS NOT NULL),
                SUM(category_truncated),
                SUM(retry_after IS NOT NULL)
            FROM asset_observations
            GROUP BY provider, status
            ORDER BY provider, status
            """
        )
        for row in grouped:
            provider, status, count, *values = row
            provider_counts[str(provider)] += int(count)
            status_counts[str(status)] += int(count)
            rows += int(count)
            for index, value in enumerate(values):
                aggregates[index] += int(value or 0)
        if duplicate_assets is None:
            duplicate_assets = int(
                connection.execute(
                    """
                    SELECT COALESCE(SUM(count - 1), 0) FROM (
                        SELECT COUNT(*) AS count FROM asset_observations
                        GROUP BY provider, canonical_reference, provider_asset_id, image_url
                    )
                    """
                ).fetchone()[0]
            )
    values = aggregates
    return {
        "shards": len(manifests),
        "rows": rows,
        "output_bytes": sum(manifest.output.size_bytes for manifest, _ in manifests),
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
        "cache_hits": sum(manifest.counts.cache_hits for manifest, _ in manifests),
        "network_resolutions": sum(manifest.counts.resolver_requests for manifest, _ in manifests),
        "asset_schema_versions": {
            str(key): value for key, value in sorted(schema_versions.items())
        },
        "resolver_contract_versions": {
            str(key): value for key, value in sorted(resolver_versions.items())
        },
    }


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
    schema_versions: Counter[int] = Counter()
    resolver_versions: Counter[int] = Counter()
    for manifest, _output in source_manifests:
        schema_versions[manifest.asset_schema_version] += 1
        resolver_versions[manifest.resolver_contract_version] += 1

    provider_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    usable_image_ids: set[str] = set()
    direct_urls = stable_direct_urls = page_urls = expiring_urls = 0
    licensed_assets = truncated_categories = pending_retries = 0
    image_rows = 0
    for batch in pq.ParquetFile(image_path).iter_batches(
        columns=[
            "image_id",
            "provider",
            "status",
            "image_url",
            "image_url_expires_at",
            "page_url",
            "license_id",
            "category_truncated",
            "retry_after",
        ],
        batch_size=65_536,
    ):
        image_rows += batch.num_rows
        columns = {name: batch.column(name).to_pylist() for name in batch.schema.names}
        for values in zip(*columns.values(), strict=True):
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
            provider_counts[str(provider)] += 1
            status_counts[str(status)] += 1
            if image_url is not None:
                direct_urls += 1
                usable_image_ids.add(str(image_id_value))
                if expires_at is None:
                    stable_direct_urls += 1
                else:
                    expiring_urls += 1
            if page_url is not None:
                page_urls += 1
            if license_id is not None:
                licensed_assets += 1
            if category_truncated:
                truncated_categories += 1
            if retry_after is not None:
                pending_retries += 1

    image_relation_counts: Counter[str] = Counter({"category_membership": 0, "direct_reference": 0})
    relationship_rows = 0
    for batch in pq.ParquetFile(link_path).iter_batches(
        columns=["image_id", "relation_kind"], batch_size=65_536
    ):
        image_ids = batch.column("image_id").to_pylist()
        relation_kinds = batch.column("relation_kind").to_pylist()
        relationship_rows += batch.num_rows
        for image_id_value, relation_kind in zip(image_ids, relation_kinds, strict=True):
            if str(image_id_value) in usable_image_ids:
                image_relation_counts[str(relation_kind)] += 1

    return {
        "shards": len(source_manifests),
        "rows": image_rows,
        "relationship_rows": relationship_rows,
        "output_bytes": image_path.stat().st_size + link_path.stat().st_size,
        "provider_counts": provider_counts,
        "status_counts": status_counts,
        "direct_urls": direct_urls,
        "stable_direct_urls": stable_direct_urls,
        "image_relation_counts": dict(sorted(image_relation_counts.items())),
        "usable_relationship_rows": sum(image_relation_counts.values()),
        "page_urls": page_urls,
        "expiring_urls": expiring_urls,
        "licensed_assets": licensed_assets,
        "truncated_categories": truncated_categories,
        "pending_retries": pending_retries,
        "duplicate_assets": duplicate_images,
        "duplicate_assets_removed": duplicate_images,
        "duplicate_images_removed": duplicate_images,
        "duplicate_links_removed": duplicate_links,
        "orphan_rows": orphan_rows,
        "cache_hits": sum(manifest.counts.cache_hits for manifest, _ in source_manifests),
        "network_resolutions": sum(
            manifest.counts.resolver_requests for manifest, _ in source_manifests
        ),
        "asset_schema_versions": {
            str(key): value for key, value in sorted(schema_versions.items())
        },
        "resolver_contract_versions": {
            str(key): value for key, value in sorted(resolver_versions.items())
        },
    }
