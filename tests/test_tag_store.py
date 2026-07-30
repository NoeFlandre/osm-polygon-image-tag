from pathlib import Path

import pytest

from osm_polygon_image_tag.ingest.extraction import SourceTagRecord
from osm_polygon_image_tag.ingest.tag_store import TagStore


def test_store_round_trips_exact_tags_and_count(tmp_path: Path) -> None:
    with TagStore.create(tmp_path, commit_interval=2) as store:
        store.add(SourceTagRecord("way", 1, {"image": "", "name": "Café"}))
        store.add(SourceTagRecord("relation", 2, {"type": "multipolygon", "flickr": "x"}))
        store.flush()

        assert store.count() == 2
        assert store.lookup("way", 1) == {"image": "", "name": "Café"}
        assert store.lookup("relation", 2) == {"flickr": "x", "type": "multipolygon"}
        assert store.lookup("way", 999) is None

    assert not list((tmp_path / "tmp").glob("tag-store-*"))


def test_store_rejects_duplicate_identity(tmp_path: Path) -> None:
    with TagStore.create(tmp_path) as store:
        store.add(SourceTagRecord("way", 1, {"image": "a"}))
        with pytest.raises(ValueError, match="duplicate"):
            store.add(SourceTagRecord("way", 1, {"image": "b"}))


def test_context_cleans_database_after_exception(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="stop"), TagStore.create(tmp_path) as store:
        store.add(SourceTagRecord("way", 1, {"image": "a"}))
        raise RuntimeError("stop")

    assert not list((tmp_path / "tmp").glob("tag-store-*"))
