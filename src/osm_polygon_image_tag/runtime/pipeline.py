import hashlib
import os
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from osm_polygon_image_tag.artifacts.storage import validate_geoparquet, write_geoparquet
from osm_polygon_image_tag.core.config import PipelinePaths
from osm_polygon_image_tag.core.manifest import (
    DATASET_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    PROCESSING_CONTRACT_VERSION,
    Manifest,
    ManifestError,
    OutputIdentity,
    RunCounts,
    file_sha256,
    read_manifest,
    source_identity,
    write_manifest,
)
from osm_polygon_image_tag.ingest.discovery import PbfSource
from osm_polygon_image_tag.ingest.extraction import (
    ExportRecord,
    SourceTagRecord,
    osmium_version,
    restore_original_tags,
    scan_target_source_tags,
    stream_export,
)
from osm_polygon_image_tag.ingest.tag_store import TagStore
from osm_polygon_image_tag.ingest.transform import AcceptedRow, RejectedRow, transform_records
from osm_polygon_image_tag.runtime.resources import osmium_export_config

Scanner = Callable[..., None]
Exporter = Callable[..., Iterable[ExportRecord]]
VersionGetter = Callable[..., str]
_TAG_STORE_BATCH_SIZE = 1000


@dataclass(frozen=True, slots=True)
class BuildResult:
    status: str
    source_pbf: str
    output_path: Path
    manifest_path: Path
    accepted_rows: int
    rejections: dict[str, int]


def _artifact_stem(source: PbfSource) -> str:
    relative = source.relative_path.as_posix()
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", relative.removesuffix(".osm.pbf"))
    digest = hashlib.sha256(relative.encode()).hexdigest()[:12]
    return f"{readable}-{digest}"


def artifact_paths(source: PbfSource, data_root: Path) -> tuple[Path, Path]:
    stem = _artifact_stem(source)
    return (
        data_root / "data" / f"{stem}.parquet",
        data_root / "manifests" / f"{stem}.manifest.json",
    )


def _is_reusable(
    manifest_path: Path,
    output_path: Path,
    *,
    source: PbfSource,
    deep: bool = False,
) -> Manifest | None:
    try:
        manifest = read_manifest(manifest_path)
        source_stat = source.absolute_path.stat()
        if not _reusable_metadata_matches(
            manifest, source, source_stat, manifest_path, output_path
        ):
            return None
        if deep and not _deep_reusable_matches(manifest, source, output_path):
            return None
        return manifest
    except (ManifestError, OSError, ValueError):
        return None


def _reusable_metadata_matches(
    manifest: Manifest,
    source: PbfSource,
    source_stat: os.stat_result,
    manifest_path: Path,
    output_path: Path,
) -> bool:
    metadata_matches = not any(
        (
            manifest.processing_contract_version != PROCESSING_CONTRACT_VERSION,
            manifest.dataset_schema_version != DATASET_SCHEMA_VERSION,
            manifest.source.relative_path != source.relative_path.as_posix(),
            manifest.source.size_bytes != source_stat.st_size,
            manifest.source.mtime_ns != source_stat.st_mtime_ns,
            manifest.output.relative_path
            != output_path.relative_to(manifest_path.parents[1]).as_posix(),
        )
    )
    return metadata_matches and _output_matches(output_path, manifest.output.size_bytes)


def _output_matches(output_path: Path, expected_size: int) -> bool:
    return (
        not output_path.is_symlink()
        and output_path.is_file()
        and output_path.stat().st_size == expected_size
    )


def _deep_reusable_matches(manifest: Manifest, source: PbfSource, output_path: Path) -> bool:
    if (
        source_identity(
            source.absolute_path,
            relative_path=source.relative_path.as_posix(),
        )
        != manifest.source
    ):
        return False
    if file_sha256(output_path) != manifest.output.sha256:
        return False
    validate_geoparquet(output_path)
    return pq.ParquetFile(output_path).metadata.num_rows == manifest.output.row_count


def build_one(
    source: PbfSource,
    paths: PipelinePaths,
    *,
    scanner: Scanner = scan_target_source_tags,
    exporter: Exporter = stream_export,
    version_getter: VersionGetter = osmium_version,
    executable: str = "osmium",
    batch_size: int = 4096,
) -> BuildResult:
    output_path, manifest_path = artifact_paths(source, paths.data_root)
    reusable = _is_reusable(manifest_path, output_path, source=source)
    if reusable is not None:
        return BuildResult(
            status="skipped",
            source_pbf=source.relative_path.as_posix(),
            output_path=output_path,
            manifest_path=manifest_path,
            accepted_rows=reusable.counts.accepted_rows,
            rejections=dict(reusable.counts.rejections),
        )

    source_value = source_identity(
        source.absolute_path,
        relative_path=source.relative_path.as_posix(),
    )
    accepted_rows = 0
    rejections: Counter[str] = Counter()
    with TagStore.create(paths.data_root) as tags:
        pending_tags: list[SourceTagRecord] = []

        def emit_tag(record: SourceTagRecord) -> None:
            pending_tags.append(record)
            if len(pending_tags) == _TAG_STORE_BATCH_SIZE:
                tags.add_many(pending_tags)
                pending_tags.clear()

        scanner(source.absolute_path, emit=emit_tag)
        if pending_tags:
            tags.add_many(pending_tags)
        tags.flush()
        records = restore_original_tags(
            exporter(
                source.absolute_path,
                osmium_export_config(),
                executable=executable,
            ),
            lookup=tags.lookup,
            lookup_many=tags.lookup_many,
        )

        def rows() -> Iterator[dict[str, object]]:
            nonlocal accepted_rows
            for outcome in transform_records(records, source_pbf=source.relative_path.as_posix()):
                if isinstance(outcome, RejectedRow):
                    rejections[outcome.reason] += 1
                    continue
                assert isinstance(outcome, AcceptedRow)
                accepted_rows += 1
                yield outcome.values

        write_result = write_geoparquet(rows(), output_path, batch_size=batch_size)

    output = OutputIdentity(
        relative_path=output_path.relative_to(paths.data_root).as_posix(),
        size_bytes=write_result.size_bytes,
        sha256=file_sha256(output_path),
        row_count=write_result.row_count,
    )
    counts = RunCounts(
        accepted_rows=accepted_rows,
        rejections=dict(sorted(rejections.items())),
    )
    manifest = Manifest(
        manifest_schema_version=MANIFEST_SCHEMA_VERSION,
        processing_contract_version=PROCESSING_CONTRACT_VERSION,
        dataset_schema_version=DATASET_SCHEMA_VERSION,
        source=source_value,
        output=output,
        osmium_version=version_getter(executable=executable),
        counts=counts,
    )
    write_manifest(manifest, manifest_path)
    return BuildResult(
        status="built",
        source_pbf=source.relative_path.as_posix(),
        output_path=output_path,
        manifest_path=manifest_path,
        accepted_rows=accepted_rows,
        rejections=counts.rejections,
    )


def verify_one(source: PbfSource, paths: PipelinePaths) -> bool:
    output_path, manifest_path = artifact_paths(source, paths.data_root)
    return _is_reusable(manifest_path, output_path, source=source, deep=True) is not None
