import sqlite3
from collections import Counter
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
