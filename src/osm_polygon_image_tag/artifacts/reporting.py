import json
from dataclasses import dataclass
from pathlib import Path

from osm_polygon_image_tag.artifacts.asset_catalog import sync_asset_catalog
from osm_polygon_image_tag.artifacts.asset_inventory import verified_asset_manifests
from osm_polygon_image_tag.artifacts.asset_statistics import asset_statistics
from osm_polygon_image_tag.artifacts.catalog import sync_catalog
from osm_polygon_image_tag.artifacts.dataset_card import dataset_card
from osm_polygon_image_tag.artifacts.geography.pipeline import build_geographic_map
from osm_polygon_image_tag.artifacts.hero import HERO_PNG_RELATIVE, packaged_hero_path
from osm_polygon_image_tag.artifacts.manifest_inventory import verified_manifests
from osm_polygon_image_tag.artifacts.statistics import dataset_statistics
from osm_polygon_image_tag.core.atomic import atomic_write_bytes
from osm_polygon_image_tag.core.progress import Progress


@dataclass(frozen=True, slots=True)
class MetadataResult:
    statistics_path: Path
    card_path: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "statistics_path": str(self.statistics_path),
            "card_path": str(self.card_path),
        }


def _atomic_write(path: Path, content: bytes) -> None:
    atomic_write_bytes(path, content, prefix=f".{path.name}.", suffix=".tmp")


def _sync_hero(data_root: Path) -> None:
    target = data_root / HERO_PNG_RELATIVE
    content = packaged_hero_path().read_bytes()
    if target.is_file() and target.read_bytes() == content:
        return
    _atomic_write(target, content)


def generate_metadata(data_root: Path, *, progress: Progress | None = None) -> MetadataResult:
    emit = progress or (lambda _event: None)
    manifests = verified_manifests(data_root, progress=emit)
    asset_manifests = verified_asset_manifests(data_root, progress=emit)
    catalog_path = sync_catalog(data_root, manifests=manifests, progress=emit)
    sync_asset_catalog(catalog_path, asset_manifests, progress=emit)
    emit({"event": "metadata_statistics_started"})
    statistics = dataset_statistics(catalog_path, manifests)
    statistics["assets"] = asset_statistics(catalog_path, asset_manifests)
    emit(
        {
            "event": "metadata_statistics_completed",
            "shards": statistics["shards"],
            "rows": statistics["rows"],
        }
    )
    map_result = build_geographic_map(data_root, progress=emit)
    statistics["geography"] = map_result.statistics.to_dict()
    _sync_hero(data_root)
    statistics_path = data_root / "statistics" / "dataset-statistics.json"
    card_path = data_root / "README.md"
    serialized = (
        json.dumps(statistics, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")
    emit({"event": "metadata_write_started"})
    _atomic_write(statistics_path, serialized)
    _atomic_write(card_path, dataset_card(statistics))
    emit(
        {
            "event": "metadata_write_completed",
            "statistics_path": str(statistics_path),
            "card_path": str(card_path),
        }
    )
    return MetadataResult(statistics_path=statistics_path, card_path=card_path)
