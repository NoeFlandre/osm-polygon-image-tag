from pathlib import Path

import pytest

from osm_polygon_image_tag.ingest import tag_store
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


def test_store_looks_up_many_pending_rows_without_reordering(tmp_path: Path) -> None:
    with TagStore.create(tmp_path) as store:
        store.add(SourceTagRecord("way", 1, {"image": "one"}))
        store.add(SourceTagRecord("relation", 2, {"flickr": "two"}))

        assert store.lookup_many([("relation", 2), ("way", 999), ("way", 1), ("relation", 2)]) == {
            ("relation", 2): {"flickr": "two"},
            ("way", 1): {"image": "one"},
        }


def test_store_splits_large_lookup_requests_at_sqlite_parameter_limit(tmp_path: Path) -> None:
    with TagStore.create(tmp_path) as store:
        for osm_id in range(500):
            store.add(SourceTagRecord("way", osm_id, {"image": str(osm_id)}))

        found = store.lookup_many(("way", osm_id) for osm_id in range(500))

    assert len(found) == 500
    assert found[("way", 0)] == {"image": "0"}
    assert found[("way", 499)] == {"image": "499"}


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


def test_store_delegates_tag_encoding_to_canonical_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canonical_json = getattr(tag_store, "canonical_json", None)
    assert canonical_json is not None
    encoded_values: list[object] = []

    def record_encoding(value: object) -> str:
        encoded_values.append(value)
        return canonical_json(value)

    monkeypatch.setattr(tag_store, "canonical_json", record_encoding)
    tags = {"image": "https://example.test/image.jpg", "name": "Café"}

    with TagStore.create(tmp_path) as store:
        store.add(SourceTagRecord("way", 1, tags))

    assert encoded_values == [tags]
