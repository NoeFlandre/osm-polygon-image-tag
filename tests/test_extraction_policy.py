import json

from osm_polygon_image_tag.resources import osmium_export_config


def test_packaged_osmium_policy_preserves_provenance_and_all_tags() -> None:
    path = osmium_export_config()
    config = json.loads(path.read_text(encoding="utf-8"))

    assert config["attributes"] == {
        "type": "__osm_type",
        "id": "__osm_id",
        "version": "__osm_version",
        "changeset": "__osm_changeset",
        "timestamp": "__osm_timestamp",
        "uid": False,
        "user": False,
        "way_nodes": False,
    }
    assert config["format_options"] == {"tags_type": "json"}
    assert config["linear_tags"] is True
    assert config["area_tags"] is True
    assert config["exclude_tags"] == []
    assert config["include_tags"] == []
