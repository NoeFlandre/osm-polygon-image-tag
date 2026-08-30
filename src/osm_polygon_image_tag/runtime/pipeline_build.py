"""Build one source shard after the runtime pipeline selects it."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from osm_polygon_image_tag.artifacts.storage import WriteResult, write_geoparquet
from osm_polygon_image_tag.core.manifest import RunCounts
from osm_polygon_image_tag.ingest.discovery import PbfSource
from osm_polygon_image_tag.ingest.extraction import (
    ExportRecord,
    SourceTagRecord,
    restore_original_tags,
)
from osm_polygon_image_tag.ingest.tag_store import TagStore
from osm_polygon_image_tag.ingest.transform import AcceptedRow, RejectedRow, transform_records
from osm_polygon_image_tag.runtime.resources import osmium_export_config

Scanner = Callable[..., None]
Exporter = Callable[..., Iterable[ExportRecord]]
_TAG_STORE_BATCH_SIZE = 1000


def build_source_output(
    source: PbfSource,
    *,
    data_root: Path,
    output_path: Path,
    scanner: Scanner,
    exporter: Exporter,
    executable: str,
    batch_size: int,
) -> tuple[WriteResult, RunCounts]:
    """Scan, restore, transform, and write one source shard."""
    with TagStore.create(data_root) as tags:
        _scan_source_tags(source.absolute_path, scanner, tags)
        records = restore_original_tags(
            exporter(
                source.absolute_path,
                osmium_export_config(),
                executable=executable,
            ),
            lookup=tags.lookup,
            lookup_many=tags.lookup_many,
        )
        return _write_transformed_output(
            records,
            source_pbf=source.relative_path.as_posix(),
            output_path=output_path,
            batch_size=batch_size,
        )


def _scan_source_tags(
    source_path: Path,
    scanner: Scanner,
    tags: TagStore,
) -> None:
    pending_tags: list[SourceTagRecord] = []

    def emit_tag(record: SourceTagRecord) -> None:
        pending_tags.append(record)
        if len(pending_tags) == _TAG_STORE_BATCH_SIZE:
            tags.add_many(pending_tags)
            pending_tags.clear()

    scanner(source_path, emit=emit_tag)
    if pending_tags:
        tags.add_many(pending_tags)
    tags.flush()


def _write_transformed_output(
    records: Iterable[ExportRecord],
    *,
    source_pbf: str,
    output_path: Path,
    batch_size: int,
) -> tuple[WriteResult, RunCounts]:
    accepted_rows = 0
    rejections: Counter[str] = Counter()

    def rows() -> Iterator[dict[str, object]]:
        nonlocal accepted_rows
        for outcome in transform_records(records, source_pbf=source_pbf):
            if isinstance(outcome, RejectedRow):
                rejections[outcome.reason] += 1
                continue
            assert isinstance(outcome, AcceptedRow)
            accepted_rows += 1
            yield outcome.values

    write_result = write_geoparquet(rows(), output_path, batch_size=batch_size)
    return write_result, RunCounts(
        accepted_rows=accepted_rows,
        rejections=dict(sorted(rejections.items())),
    )
