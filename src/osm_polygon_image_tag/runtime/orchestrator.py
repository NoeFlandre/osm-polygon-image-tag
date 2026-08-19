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
from osm_polygon_image_tag.runtime.cleanup import cleanup_stale_temps
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
    refresh_lock: threading.Lock,
) -> list[BuildResult]:
    results: list[BuildResult] = []
    worker_started = False
    try:
        worker_started = _start_enrichment_worker(enrichment_worker, paths, emit)
        for index, source in enumerate(sources, start=1):
            if token.requested:
                break
            results.append(
                _build_source(
                    source,
                    index,
                    sources,
                    paths,
                    build=build,
                    emit=emit,
                    enrichment_worker=enrichment_worker,
                    metadata_builder=metadata_builder,
                    publisher=publisher,
                    refresh_lock=refresh_lock,
                )
            )
    except BaseException:
        token.request()
        _finish_worker_after_error(worker_started, enrichment_worker)
        raise
    return results


def _finish_worker_after_error(worker_started: bool, worker: EnrichmentController | None) -> None:
    if not worker_started or worker is None:
        return
    with suppress(BaseException):
        worker.finish()


def _start_enrichment_worker(
    worker: EnrichmentController | None, paths: PipelinePaths, emit: Progress
) -> bool:
    if worker is None:
        return False
    worker.start(
        AssetJob(manifest, output)
        for manifest, output in verified_manifests(paths.data_root, progress=emit)
    )
    return True


def _build_source(
    source: PbfSource,
    index: int,
    sources: Sequence[PbfSource],
    paths: PipelinePaths,
    *,
    build: Build,
    emit: Progress,
    enrichment_worker: EnrichmentController | None,
    metadata_builder: MetadataBuilder,
    publisher: Publisher | None,
    refresh_lock: threading.Lock,
) -> BuildResult:
    emit(
        {
            "event": "pbf_started",
            "pbf_index": index,
            "pbf_count": len(sources),
            "source_pbf": source.relative_path.as_posix(),
            "source_bytes": source.size_bytes,
        }
    )
    with refresh_lock:
        result = build(source, paths)
    _submit_asset_job(enrichment_worker, result)
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

    def refresh() -> None:
        _refresh_artifacts(
            paths,
            emit,
            metadata_builder=metadata_builder,
            publisher=publisher,
            refresh_lock=refresh_lock,
        )

    if _should_checkpoint_assets(result, enrichment_worker, publisher):
        assert enrichment_worker is not None
        enrichment_worker.checkpoint(refresh)
    if _should_refresh_without_worker(result, enrichment_worker):
        refresh()
    return result


def _should_checkpoint_assets(
    result: BuildResult,
    enrichment_worker: EnrichmentController | None,
    publisher: Publisher | None,
) -> bool:
    return result.status == "built" and enrichment_worker is not None and publisher is not None


def _should_refresh_without_worker(
    result: BuildResult, enrichment_worker: EnrichmentController | None
) -> bool:
    return result.status != "skipped" and enrichment_worker is None


def _submit_asset_job(worker: EnrichmentController | None, result: BuildResult) -> None:
    if worker is not None and result.manifest_path.is_file():
        worker.submit(AssetJob(read_manifest(result.manifest_path), result.output_path))


def _refresh_artifacts(
    paths: PipelinePaths,
    emit: Progress,
    *,
    metadata_builder: MetadataBuilder | None,
    publisher: Publisher | None,
    refresh_lock: threading.Lock,
) -> None:
    with refresh_lock:
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
    _prepare_run(paths, emit)
    emit(
        {
            "event": "run_started",
            "pbf_count": len(sources),
            "pbf_bytes": _source_bytes(sources),
        }
    )
    refresh_lock = threading.Lock()
    _enable_enrichment_checkpoints(
        enrichment_worker,
        publisher,
        paths,
        emit,
        metadata_builder,
        refresh_lock,
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
        refresh_lock=refresh_lock,
    )
    enrichment = _finish_enrichment(enrichment_worker)
    needs_final_artifacts = _needs_final_artifacts(results, enrichment_worker, enrichment)
    if needs_final_artifacts:
        _refresh_artifacts(
            paths,
            emit,
            metadata_builder=metadata_builder,
            publisher=publisher,
            refresh_lock=refresh_lock,
        )
    summary = RunSummary(
        processed=len(results),
        built=_count_status(results, "built"),
        skipped=_count_status(results, "skipped"),
        accepted_rows=_accepted_rows(results),
        stopped=token.requested,
        enrichment=enrichment,
    )
    emit({"event": "run_completed", **summary.to_dict()})
    return summary


def _source_bytes(sources: Sequence[PbfSource]) -> int:
    return sum(source.size_bytes for source in sources)


def _finish_enrichment(worker: EnrichmentController | None) -> EnrichmentSummary:
    return worker.finish() if worker is not None else EnrichmentSummary()


def _count_status(results: Sequence[BuildResult], status: str) -> int:
    return sum(result.status == status for result in results)


def _accepted_rows(results: Sequence[BuildResult]) -> int:
    return sum(result.accepted_rows for result in results)


def _prepare_run(paths: PipelinePaths, emit: Progress) -> None:
    removed_temps = cleanup_stale_temps(paths.data_root)
    if removed_temps:
        emit({"event": "temporary_cleanup", "removed": len(removed_temps)})


def _enable_enrichment_checkpoints(
    worker: EnrichmentController | None,
    publisher: Publisher | None,
    paths: PipelinePaths,
    emit: Progress,
    metadata_builder: MetadataBuilder,
    refresh_lock: threading.Lock,
) -> None:
    if worker is None or publisher is None:
        return
    worker.enable_checkpoints(
        lambda: _refresh_artifacts(
            paths,
            emit,
            metadata_builder=metadata_builder,
            publisher=publisher,
            refresh_lock=refresh_lock,
        ),
        every=1,
    )


def _needs_final_artifacts(
    results: Sequence[BuildResult],
    worker: EnrichmentController | None,
    enrichment: EnrichmentSummary,
) -> bool:
    return not results or (
        worker is not None
        and (any(result.status == "built" for result in results) or enrichment.built > 0)
    )


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
