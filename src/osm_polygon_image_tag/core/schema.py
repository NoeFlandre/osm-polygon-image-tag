import json

import pyarrow as pa
from pyproj import CRS

from osm_polygon_image_tag.core.contracts import PANORAMAX_VALUES_COLUMN, REFERENCE_COLUMNS

SCHEMA_VERSION = 3
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


def _reference_fields() -> list[pa.Field]:
    fields: list[pa.Field] = []
    for name in REFERENCE_COLUMNS:
        if name == PANORAMAX_VALUES_COLUMN:
            fields.append(
                pa.field(
                    name,
                    pa.list_(
                        pa.struct(
                            [
                                pa.field("key", pa.string(), nullable=False),
                                pa.field("value", pa.string(), nullable=False),
                            ]
                        )
                    ),
                    nullable=False,
                )
            )
        else:
            fields.append(pa.field(name, pa.string(), nullable=True))
    return fields


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
        pa.field(
            "tags",
            pa.list_(
                pa.struct(
                    [
                        pa.field("key", pa.string(), nullable=False),
                        pa.field("value", pa.string(), nullable=False),
                    ]
                )
            ),
            nullable=False,
        ),
    ]
    fields.extend(_reference_fields())
    return pa.schema(
        fields,
        metadata={
            b"geo": _geo_metadata(),
            b"osm_polygon_image_tag_schema_version": str(SCHEMA_VERSION).encode(),
        },
    )
