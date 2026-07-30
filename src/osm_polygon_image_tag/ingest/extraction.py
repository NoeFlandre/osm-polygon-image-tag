"""Stable ingest API composed from focused parsing, tag, and osmium modules."""

from osm_polygon_image_tag.ingest.copy_parser import (
    ExportRecord,
    iter_records,
    parse_copy_record,
)
from osm_polygon_image_tag.ingest.osmium import (
    STDERR_CAP_BYTES,
    OsmiumExportError,
    export_command,
    osmium_version,
    stream_export,
)
from osm_polygon_image_tag.ingest.tag_policy import (
    TARGET_TAG_KEYS,
    SourceTagRecord,
    has_target_tag,
    is_target_tag_key,
    panoramax_tag_values,
    restore_original_tags,
    scan_target_source_tags,
)

__all__ = [
    "STDERR_CAP_BYTES",
    "TARGET_TAG_KEYS",
    "ExportRecord",
    "OsmiumExportError",
    "SourceTagRecord",
    "export_command",
    "has_target_tag",
    "is_target_tag_key",
    "iter_records",
    "osmium_version",
    "panoramax_tag_values",
    "parse_copy_record",
    "restore_original_tags",
    "scan_target_source_tags",
    "stream_export",
]
