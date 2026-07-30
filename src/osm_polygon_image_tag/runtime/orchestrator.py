import signal
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from osm_polygon_image_tag.artifacts.asset_verify import verify_assets
from osm_polygon_image_tag.artifacts.manifest_inventory import verified_manifests
from osm_polygon_image_tag.artifacts.publication import PublicationResult
from osm_polygon_image_tag.artifacts.reporting import MetadataResult, generate_metadata
from osm_polygon_image_tag.core.config import PipelinePaths
from osm_polygon_image_tag.core.manifest import read_manifest
from osm_polygon_image_tag.ingest.discovery import PbfSource, discover_pbfs
from osm_polygon_image_tag.runtime.enrichment import (
    AssetJob,
    EnrichmentSummary,
)
from osm_polygon_image_tag.runtime.pipeline import BuildResult, build_one, verify_one

Build = Callable[[PbfSource, PipelinePaths], BuildResult]
MetadataBuilder = Callable[[Path], MetadataResult]
Publisher = Callable[[Path], PublicationResult]
Progress = Callable[[dict[str, object]], None]


class EnrichmentController(Protocol):
    def start(self, initial_jobs: Iterable[AssetJob]) -> None: ...
    def submit(self, job: AssetJob) -> bool: ...
    def enable_checkpoints(self, callback: Callable[[], None], *, every: int) -> None: ...
    def checkpoint(self, callback: Callable[[], None]) -> None: ...
    def finish(self) -> EnrichmentSummary: ...


class StopToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()


@dataclass(frozen=True, slots=True)
class RunSummary:
    processed: int
    built: int
    skipped: int
    accepted_rows: int
    stopped: bool
    enrichment: EnrichmentSummary = field(default_factory=EnrichmentSummary)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VerifySummary:
    checked: int
    valid: int
    invalid: int
    asset_checked: int = 0
    asset_valid: int = 0
    asset_invalid: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build_sources(
    sources: Sequence[PbfSource],
    paths: PipelinePaths,
    *,
    build: Build,
    token: StopToken,
    emit: Progress,
    enrichment_worker: EnrichmentController | None,
    metadata_builder: MetadataBuilder,
    publisher: Publisher | None,
) -> list[BuildResult]:
    results: list[BuildResult] = []
    worker_started = False
    try:
        if enrichment_worker is not None:
            worker_started = True
            enrichment_worker.start(
                AssetJob(manifest, output)
                for manifest, output in verified_manifests(paths.data_root, progress=emit)
            )
        for index, source in enumerate(sources, start=1):
            if token.requested:
                break
            emit(
                {
                    "event": "pbf_started",
                    "pbf_index": index,
                    "pbf_count": len(sources),
                    "source_pbf": source.relative_path.as_posix(),
                    "source_bytes": source.size_bytes,
                }
            )
            result = build(source, paths)
            results.append(result)
            if enrichment_worker is not None and result.manifest_path.is_file():
                enrichment_worker.submit(
                    AssetJob(read_manifest(result.manifest_path), result.output_path)
                )
            emit(
                {
                    "event": "pbf_completed",
                    "pbf_index": index,
                    "pbf_count": len(sources),
                    "source_pbf": result.source_pbf,
                    "status": result.status,
                    "accepted_rows": result.accepted_rows,
                    "rejections": result.rejections,
                }
            )
            if result.status == "built" and enrichment_worker is not None and publisher is not None:
                enrichment_worker.checkpoint(
                    lambda: _refresh_artifacts(
                        paths,
                        emit,
                        metadata_builder=metadata_builder,
                        publisher=publisher,
                    )
                )
            if result.status == "skipped" or enrichment_worker is not None:
                continue
            _refresh_artifacts(
                paths,
                emit,
                metadata_builder=metadata_builder,
                publisher=publisher,
            )
    except BaseException:
        token.request()
        if worker_started and enrichment_worker is not None:
            with suppress(BaseException):
                enrichment_worker.finish()
        raise
    return results


def _refresh_artifacts(
    paths: PipelinePaths,
    emit: Progress,
    *,
    metadata_builder: MetadataBuilder | None,
    publisher: Publisher | None,
) -> None:
    builder = metadata_builder or generate_metadata
    emit({"event": "metadata_started"})
    metadata = builder(paths.data_root)
    emit(
        {
            "event": "metadata_completed",
            "statistics_path": str(metadata.statistics_path),
            "card_path": str(metadata.card_path),
        }
    )
    if publisher is not None:
        emit({"event": "publication_started"})
        publication = publisher(paths.data_root)
        emit({"event": "publication_completed", **publication.to_dict()})


def run_all(
    paths: PipelinePaths,
    *,
    build: Build = build_one,
    stop_token: StopToken | None = None,
    metadata_builder: MetadataBuilder = generate_metadata,
    publisher: Publisher | None = None,
    progress: Progress | None = None,
    enrichment_worker: EnrichmentController | None = None,
) -> RunSummary:
    token = stop_token or StopToken()
    sources = discover_pbfs(paths.source_root)
    emit = progress or (lambda _event: None)
    emit(
        {
            "event": "run_started",
            "pbf_count": len(sources),
            "pbf_bytes": sum(source.size_bytes for source in sources),
        }
    )
    results = _build_sources(
        sources,
        paths,
        build=build,
        token=token,
        emit=emit,
        enrichment_worker=enrichment_worker,
        metadata_builder=metadata_builder,
        publisher=publisher,
    )
    if enrichment_worker is not None and publisher is not None:
        enrichment_worker.enable_checkpoints(
            lambda: _refresh_artifacts(
                paths,
                emit,
                metadata_builder=metadata_builder,
                publisher=publisher,
            ),
            every=25,
        )
    enrichment = (
        enrichment_worker.finish() if enrichment_worker is not None else EnrichmentSummary()
    )
    needs_final_artifacts = not results or (
        enrichment_worker is not None
        and (any(result.status == "built" for result in results) or enrichment.built > 0)
    )
    if needs_final_artifacts:
        _refresh_artifacts(
            paths,
            emit,
            metadata_builder=metadata_builder,
            publisher=publisher,
        )
    summary = RunSummary(
        processed=len(results),
        built=sum(result.status == "built" for result in results),
        skipped=sum(result.status == "skipped" for result in results),
        accepted_rows=sum(result.accepted_rows for result in results),
        stopped=token.requested,
        enrichment=enrichment,
    )
    emit({"event": "run_completed", **summary.to_dict()})
    return summary


def verify_all(paths: PipelinePaths) -> VerifySummary:
    results = [verify_one(source, paths) for source in discover_pbfs(paths.source_root)]
    valid = sum(results)
    assets = verify_assets(paths.data_root)
    return VerifySummary(
        checked=len(results),
        valid=valid,
        invalid=len(results) - valid,
        asset_checked=assets.checked,
        asset_valid=assets.valid,
        asset_invalid=assets.invalid,
    )


@contextmanager
def graceful_stop_signals(token: StopToken) -> Iterator[None]:
    previous: dict[signal.Signals, Any] = {}

    def request_stop(_signum: int, _frame: object) -> None:
        token.request()

    for selected in (signal.SIGINT, signal.SIGTERM):
        previous[selected] = signal.getsignal(selected)
        signal.signal(selected, request_stop)
    try:
        yield
    finally:
        for selected, handler in previous.items():
            signal.signal(selected, handler)
