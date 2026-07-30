"""Select image-reference tags and restore exact source-object tags."""

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import osmium

from osm_polygon_image_tag.ingest.copy_parser import ExportRecord

TARGET_TAG_KEYS = (
    "image",
    "wikimedia_commons",
    "mapillary",
    "panoramax",
    "kartaview",
    "flickr",
    "bubbleid",
)


@dataclass(frozen=True, slots=True)
class SourceTagRecord:
    osm_type: str
    osm_id: int
    tags: dict[str, str]


def is_target_tag_key(key: str) -> bool:
    if key in TARGET_TAG_KEYS:
        return True
    prefix = "panoramax:"
    suffix = key.removeprefix(prefix)
    return key.startswith(prefix) and suffix.isascii() and suffix.isdigit()


def has_target_tag(tags: Mapping[str, str]) -> bool:
    return any(value != "" and is_target_tag_key(key) for key, value in tags.items())


def panoramax_tag_values(tags: Mapping[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in sorted(tags.items())
        if value != ""
        and (key == "panoramax" or (is_target_tag_key(key) and key.startswith("panoramax:")))
    }


class _SourceTagHandler(osmium.SimpleHandler):
    def __init__(self, emit: Callable[[SourceTagRecord], None]) -> None:
        super().__init__()
        self._emit = emit

    def _handle(self, osm_type: str, osm_object: Any) -> None:
        tags = dict(osm_object.tags)
        if has_target_tag(tags):
            self._emit(
                SourceTagRecord(
                    osm_type=osm_type,
                    osm_id=int(osm_object.id),
                    tags=tags,
                )
            )

    def way(self, way: Any) -> None:
        self._handle("way", way)

    def relation(self, relation: Any) -> None:
        self._handle("relation", relation)


def scan_target_source_tags(
    pbf_path: Path,
    *,
    emit: Callable[[SourceTagRecord], None],
) -> None:
    _SourceTagHandler(emit).apply_file(str(pbf_path), locations=False)


def restore_original_tags(
    records: Iterable[ExportRecord],
    *,
    lookup: Callable[[str, int], Mapping[str, str] | None],
) -> Iterator[ExportRecord]:
    for record in records:
        tags = lookup(record.osm_type, record.osm_id)
        if tags is not None:
            yield replace(record, tags=dict(tags))
