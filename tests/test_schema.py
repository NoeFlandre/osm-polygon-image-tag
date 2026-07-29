import json

import pyarrow as pa

from osm_polygon_image_tag.schema import dataset_schema


def test_dataset_schema_has_exact_columns_and_nullability() -> None:
    schema = dataset_schema()

    assert schema.names == [
        "osm_type",
        "osm_id",
        "osm_version",
        "osm_changeset",
        "osm_timestamp",
        "source_pbf",
        "source_feature_id",
        "geometry",
        "geometry_type",
        "area_m2",
        "bbox_min_lon",
        "bbox_min_lat",
        "bbox_max_lon",
        "bbox_max_lat",
        "tags",
        "image",
        "wikimedia_commons",
        "mapillary",
        "panoramax",
        "panoramax_values",
        "kartaview",
        "flickr",
        "bubbleid",
    ]
    nullable = {field.name: field.nullable for field in schema}
    assert nullable == {
        "osm_type": False,
        "osm_id": False,
        "osm_version": True,
        "osm_changeset": True,
        "osm_timestamp": True,
        "source_pbf": False,
        "source_feature_id": False,
        "geometry": False,
        "geometry_type": False,
        "area_m2": False,
        "bbox_min_lon": False,
        "bbox_min_lat": False,
        "bbox_max_lon": False,
        "bbox_max_lat": False,
        "tags": False,
        "image": True,
        "wikimedia_commons": True,
        "mapillary": True,
        "panoramax": True,
        "panoramax_values": False,
        "kartaview": True,
        "flickr": True,
        "bubbleid": True,
    }
    assert schema.field("tags").type == pa.map_(pa.string(), pa.string(), keys_sorted=True)
    assert schema.field("panoramax_values").type == pa.map_(
        pa.string(), pa.string(), keys_sorted=True
    )


def test_schema_contains_geoparquet_1_1_crs84_wkb_metadata() -> None:
    metadata = dataset_schema().metadata
    assert metadata is not None
    geo = json.loads(metadata[b"geo"])

    assert geo["version"] == "1.1.0"
    assert geo["primary_column"] == "geometry"
    geometry = geo["columns"]["geometry"]
    assert geometry["encoding"] == "WKB"
    assert geometry["geometry_types"] == ["Polygon", "MultiPolygon"]
    assert geometry["crs"]["id"] == {"authority": "OGC", "code": "CRS84"}
