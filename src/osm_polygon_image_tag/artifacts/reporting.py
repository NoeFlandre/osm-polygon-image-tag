import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from osm_polygon_image_tag.artifacts.asset_catalog import sync_asset_catalog
from osm_polygon_image_tag.artifacts.asset_inventory import verified_asset_manifests
from osm_polygon_image_tag.artifacts.asset_statistics import asset_statistics
from osm_polygon_image_tag.artifacts.catalog import sync_catalog
from osm_polygon_image_tag.artifacts.citation import packaged_citation_path
from osm_polygon_image_tag.artifacts.dataset_card import dataset_card
from osm_polygon_image_tag.artifacts.geography.pipeline import build_geographic_map
from osm_polygon_image_tag.artifacts.hero import HERO_PNG_RELATIVE, packaged_hero_path
from osm_polygon_image_tag.artifacts.manifest_inventory import verified_manifests
from osm_polygon_image_tag.artifacts.public_dataset import build_public_dataset
from osm_polygon_image_tag.artifacts.statistics import dataset_statistics
from osm_polygon_image_tag.core.atomic import atomic_write_bytes
from osm_polygon_image_tag.core.progress import Progress

PUBLIC_CATALOG_RELATIVE = "catalog/public.sqlite"


@dataclass(frozen=True, slots=True)
class MetadataResult:
    statistics_path: Path
    card_path: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "statistics_path": str(self.statistics_path),
            "card_path": str(self.card_path),
        }


def _first_row(shards: Sequence[tuple[object, Path]]) -> dict[str, object] | None:
    """Read one deterministic example row from the first non-empty shard."""
    for _manifest, output in shards:
        parquet = pq.ParquetFile(output)
        for batch in parquet.iter_batches(batch_size=1):
            rows = batch.to_pylist()
            if rows:
                return dict(rows[0])
    return None


def _atomic_write(path: Path, content: bytes) -> None:
    atomic_write_bytes(path, content, prefix=f".{path.name}.", suffix=".tmp")


def _sync_hero(data_root: Path) -> None:
    target = data_root / HERO_PNG_RELATIVE
    content = packaged_hero_path().read_bytes()
    if target.is_file() and target.read_bytes() == content:
        return
    _atomic_write(target, content)


def _sync_citation(data_root: Path) -> None:
    target = data_root / "citation.cff"
    content = packaged_citation_path().read_bytes()
    if target.is_file() and target.read_bytes() == content:
        return
    _atomic_write(target, content)


def generate_metadata(data_root: Path, *, progress: Progress | None = None) -> MetadataResult:
    emit = progress or (lambda _event: None)
    manifests = verified_manifests(data_root, progress=emit)
    asset_manifests = verified_asset_manifests(data_root, progress=emit)
    public = build_public_dataset(
        data_root,
        manifests=manifests,
        asset_manifests=asset_manifests,
    )
    public_manifests = (
        [(public.polygon_manifest, public.polygon_path)] if public.polygon_rows else []
    )
    public_asset_manifests = (
        [(public.asset_manifest, public.asset_path)] if public.asset_rows else []
    )
    catalog_path = sync_catalog(
        data_root,
        manifests=public_manifests,
        catalog_path=data_root / PUBLIC_CATALOG_RELATIVE,
        progress=emit,
    )
    sync_asset_catalog(catalog_path, public_asset_manifests, progress=emit)
    emit({"event": "metadata_statistics_started"})
    statistics = dataset_statistics(catalog_path, public_manifests)
    statistics["shards"] = len(manifests)
    statistics["source_bytes"] = sum(manifest.source.size_bytes for manifest, _ in manifests)
    statistics["duplicate_observations"] = 0
    statistics["duplicate_observations_removed"] = public.duplicate_polygon_rows
    statistics["assets"] = asset_statistics(
        catalog_path,
        public_asset_manifests,
        duplicate_assets=public.duplicate_asset_rows,
    )
    statistics["assets"]["duplicate_assets_removed"] = public.duplicate_asset_rows
    emit(
        {
            "event": "metadata_statistics_completed",
            "shards": statistics["shards"],
            "rows": statistics["rows"],
        }
    )
    map_result = build_geographic_map(
        data_root,
        manifests=public_manifests,
        progress=emit,
    )
    statistics["geography"] = map_result.statistics.to_dict()
    examples = {
        "polygon": _first_row(public_manifests),
        "asset": _first_row(public_asset_manifests),
    }
    _sync_hero(data_root)
    _sync_citation(data_root)
    statistics_path = data_root / "statistics" / "dataset-statistics.json"
    card_path = data_root / "README.md"
    serialized = (
        json.dumps(statistics, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")
    emit({"event": "metadata_write_started"})
    _atomic_write(statistics_path, serialized)
    _atomic_write(card_path, dataset_card(statistics, examples=examples))
    emit(
        {
            "event": "metadata_write_completed",
            "statistics_path": str(statistics_path),
            "card_path": str(card_path),
        }
    )
    return MetadataResult(statistics_path=statistics_path, card_path=card_path)
