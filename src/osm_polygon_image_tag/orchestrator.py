import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from osm_polygon_image_tag.config import PipelinePaths
from osm_polygon_image_tag.discovery import PbfSource, discover_pbfs
from osm_polygon_image_tag.pipeline import BuildResult, build_one, verify_one
from osm_polygon_image_tag.publication import PublicationResult
from osm_polygon_image_tag.reporting import MetadataResult, generate_metadata

Build = Callable[[PbfSource, PipelinePaths], BuildResult]
MetadataBuilder = Callable[[Path], MetadataResult]
Publisher = Callable[[Path], PublicationResult]
Progress = Callable[[dict[str, object]], None]


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VerifySummary:
    checked: int
    valid: int
    invalid: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_all(
    paths: PipelinePaths,
    *,
    build: Build = build_one,
    stop_token: StopToken | None = None,
    metadata_builder: MetadataBuilder = generate_metadata,
    publisher: Publisher | None = None,
    progress: Progress | None = None,
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
    results: list[BuildResult] = []
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
        emit({"event": "metadata_started"})
        metadata = metadata_builder(paths.data_root)
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
    if not results:
        emit({"event": "metadata_started"})
        metadata = metadata_builder(paths.data_root)
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
    summary = RunSummary(
        processed=len(results),
        built=sum(result.status == "built" for result in results),
        skipped=sum(result.status == "skipped" for result in results),
        accepted_rows=sum(result.accepted_rows for result in results),
        stopped=token.requested,
    )
    emit({"event": "run_completed", **summary.to_dict()})
    return summary


def verify_all(paths: PipelinePaths) -> VerifySummary:
    results = [verify_one(source, paths) for source in discover_pbfs(paths.source_root)]
    valid = sum(results)
    return VerifySummary(checked=len(results), valid=valid, invalid=len(results) - valid)


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
