import json

import pyarrow as pa
from pyproj import CRS

SCHEMA_VERSION = 2
GEOPARQUET_VERSION = "1.1.0"


def _geo_metadata() -> bytes:
    payload = {
        "version": GEOPARQUET_VERSION,
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": ["Polygon", "MultiPolygon"],
                "crs": CRS.from_user_input("OGC:CRS84").to_json_dict(),
            }
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def dataset_schema() -> pa.Schema:
    fields = [
        pa.field("osm_type", pa.string(), nullable=False),
        pa.field("osm_id", pa.int64(), nullable=False),
        pa.field("osm_version", pa.int32(), nullable=True),
        pa.field("osm_changeset", pa.int64(), nullable=True),
        pa.field("osm_timestamp", pa.timestamp("ms", tz="UTC"), nullable=True),
        pa.field("source_pbf", pa.string(), nullable=False),
        pa.field("source_feature_id", pa.string(), nullable=False),
        pa.field("geometry", pa.binary(), nullable=False),
        pa.field("geometry_type", pa.string(), nullable=False),
        pa.field("area_m2", pa.float64(), nullable=False),
        pa.field("bbox_min_lon", pa.float64(), nullable=False),
        pa.field("bbox_min_lat", pa.float64(), nullable=False),
        pa.field("bbox_max_lon", pa.float64(), nullable=False),
        pa.field("bbox_max_lat", pa.float64(), nullable=False),
        pa.field("tags", pa.map_(pa.string(), pa.string(), keys_sorted=True), nullable=False),
        pa.field("image", pa.string(), nullable=True),
        pa.field("wikimedia_commons", pa.string(), nullable=True),
        pa.field("mapillary", pa.string(), nullable=True),
        pa.field("panoramax", pa.string(), nullable=True),
        pa.field(
            "panoramax_values",
            pa.map_(pa.string(), pa.string(), keys_sorted=True),
            nullable=False,
        ),
        pa.field("kartaview", pa.string(), nullable=True),
        pa.field("flickr", pa.string(), nullable=True),
        pa.field("bubbleid", pa.string(), nullable=True),
    ]
    return pa.schema(
        fields,
        metadata={
            b"geo": _geo_metadata(),
            b"osm_polygon_image_tag_schema_version": str(SCHEMA_VERSION).encode(),
        },
    )
