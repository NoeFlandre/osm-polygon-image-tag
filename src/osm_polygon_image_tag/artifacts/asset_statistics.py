import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from osm_polygon_image_tag.assets.manifest import AssetManifest


def _counts(connection: sqlite3.Connection, column: str) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in connection.execute(
            f"SELECT {column}, COUNT(*) FROM asset_observations "  # noqa: S608
            f"GROUP BY {column} ORDER BY {column}"
        )
    }


def asset_statistics(
    catalog_path: Path,
    manifests: list[tuple[AssetManifest, Path]],
) -> dict[str, Any]:
    schema_versions: Counter[int] = Counter()
    resolver_versions: Counter[int] = Counter()
    for manifest, _output in manifests:
        schema_versions[manifest.asset_schema_version] += 1
        resolver_versions[manifest.resolver_contract_version] += 1
    with sqlite3.connect(catalog_path) as connection:
        rows = int(connection.execute("SELECT COUNT(*) FROM asset_observations").fetchone()[0])
        provider_counts = _counts(connection, "provider")
        status_counts = _counts(connection, "status")
        aggregates = connection.execute(
            """
            SELECT
                SUM(image_url IS NOT NULL),
                SUM(page_url IS NOT NULL),
                SUM(expires_at IS NOT NULL),
                SUM(license_id IS NOT NULL),
                SUM(category_truncated),
                SUM(retry_after IS NOT NULL)
            FROM asset_observations
            """
        ).fetchone()
        duplicate = connection.execute(
            """
            SELECT COALESCE(SUM(count - 1), 0) FROM (
                SELECT COUNT(*) AS count FROM asset_observations
                GROUP BY provider, canonical_reference, provider_asset_id, image_url
            )
            """
        ).fetchone()[0]
    values = [int(value or 0) for value in aggregates]
    return {
        "shards": len(manifests),
        "rows": rows,
        "output_bytes": sum(manifest.output.size_bytes for manifest, _ in manifests),
        "provider_counts": provider_counts,
        "status_counts": status_counts,
        "direct_urls": values[0],
        "page_urls": values[1],
        "expiring_urls": values[2],
        "licensed_assets": values[3],
        "truncated_categories": values[4],
        "pending_retries": values[5],
        "duplicate_assets": int(duplicate),
        "asset_schema_versions": {
            str(key): value for key, value in sorted(schema_versions.items())
        },
        "resolver_contract_versions": {
            str(key): value for key, value in sorted(resolver_versions.items())
        },
    }
