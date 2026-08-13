"""Catalog indexes keep metadata migration proportional to rows, not shards."""

import sqlite3
from pathlib import Path

from osm_polygon_image_tag.artifacts.asset_catalog import sync_asset_catalog
from osm_polygon_image_tag.artifacts.catalog import sync_catalog


def test_catalog_indexes_shard_cleanup_columns(tmp_path: Path) -> None:
    catalog = sync_catalog(tmp_path, manifests=[])
    sync_asset_catalog(catalog, [])

    with sqlite3.connect(catalog) as connection:
        polygon_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(observations)")
        }
        asset_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(asset_observations)")
        }

    assert "observations_shard_idx" in polygon_indexes
    assert "asset_observations_shard_idx" in asset_indexes
