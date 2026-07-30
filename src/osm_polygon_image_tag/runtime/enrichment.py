import asyncio
import queue
import threading
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from osm_polygon_image_tag.assets.build_state import AssetBuildResult
from osm_polygon_image_tag.assets.builder import build_asset_shard
from osm_polygon_image_tag.core.manifest import Manifest
from osm_polygon_image_tag.core.progress import Progress

AssetBuilder = Callable[..., Awaitable[AssetBuildResult]]
Checkpoint = Callable[[], None]


@dataclass(frozen=True, slots=True)
class AssetJob:
    manifest: Manifest
    polygon_path: Path


@dataclass(frozen=True, slots=True)
class EnrichmentSummary:
    built: int = 0
    skipped: int = 0
    pending: int = 0
    rows: int = 0
    statuses: dict[str, int] | None = None

    def status_counts(self) -> dict[str, int]:
        return dict(self.statuses or {})


class EnrichmentWorker:
    def __init__(
        self,
        data_root: Path,
        *,
        builder: AssetBuilder = build_asset_shard,
        cache_factory: Callable[[Path], object],
        registry_factory: Callable[[], object],
        stop_requested: Callable[[], bool],
        progress: Progress,
    ) -> None:
        self._data_root = data_root
        self._builder = builder
        self._cache_factory = cache_factory
        self._registry_factory = registry_factory
        self._stop_requested = stop_requested
        self._progress = progress
        self._jobs: queue.Queue[AssetJob | None] = queue.Queue(maxsize=16)
        self._seen: set[str] = set()
        self._seen_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._summary = EnrichmentSummary()
        self._error: BaseException | None = None
        self._submitted = 0
        self._checkpoint_lock = threading.Lock()
        self._checkpoint: Checkpoint | None = None
        self._checkpoint_every = 0
        self._checkpoint_next = 0
        self._completed = 0
        self._pause_requested = threading.Event()
        self._paused = threading.Event()
        self._resume = threading.Event()
        self._resume.set()

    def start(self, initial_jobs: Iterable[AssetJob] = ()) -> None:
        if self._thread is not None:
            raise RuntimeError("enrichment worker already started")
        ordered = sorted(initial_jobs, key=lambda job: job.polygon_path.as_posix())
        self._thread = threading.Thread(
            target=self._thread_main,
            name="image-asset-enrichment",
            daemon=False,
        )
        self._thread.start()
        for job in ordered:
            self.submit(job)

    def submit(self, job: AssetJob) -> bool:
        identity = job.polygon_path.resolve().as_posix()
        with self._seen_lock:
            if identity in self._seen:
                return False
            self._seen.add(identity)
            self._submitted += 1
        while True:
            if self._error is not None:
                raise self._error
            try:
                self._jobs.put(job, timeout=0.1)
                break
            except queue.Full:
                continue
        return True

    def enable_checkpoints(self, callback: Checkpoint, *, every: int) -> None:
        if every <= 0:
            raise ValueError("checkpoint interval must be positive")
        with self._checkpoint_lock:
            self._checkpoint = callback
            self._checkpoint_every = every
            self._checkpoint_next = self._completed + every

    def checkpoint(self, callback: Checkpoint) -> None:
        if self._thread is None:
            raise RuntimeError("enrichment worker was not started")
        if not self._thread.is_alive():
            callback()
            return
        self._resume.clear()
        self._pause_requested.set()
        while self._thread.is_alive() and not self._paused.wait(timeout=0.1):
            continue
        if self._error is not None:
            self._pause_requested.clear()
            self._resume.set()
            raise self._error
        try:
            callback()
        finally:
            self._pause_requested.clear()
            self._resume.set()

    def finish(self) -> EnrichmentSummary:
        if self._thread is None:
            raise RuntimeError("enrichment worker was not started")
        while self._thread.is_alive() and self._error is None:
            try:
                self._jobs.put(None, timeout=0.1)
                break
            except queue.Full:
                continue
        self._thread.join()
        if self._error is not None:
            raise self._error
        return self._summary

    def _thread_main(self) -> None:
        try:
            self._summary = asyncio.run(self._run())
        except BaseException as error:
            self._error = error

    async def _run(self) -> EnrichmentSummary:
        cache = self._cache_factory(self._data_root)
        registry = self._registry_factory()
        built = skipped = pending = rows = index = 0
        statuses: Counter[str] = Counter()
        self._progress(
            {
                "event": "asset_backfill_started",
                "asset_count": self._submitted,
            }
        )
        try:
            while True:
                if self._pause_requested.is_set():
                    self._paused.set()
                    self._resume.wait()
                    self._paused.clear()
                try:
                    job = self._jobs.get(timeout=0.1)
                except queue.Empty:
                    continue
                if job is None:
                    break
                index += 1
                if self._stop_requested():
                    pending += 1
                    continue
                self._progress(
                    {
                        "event": "asset_shard_started",
                        "asset_index": index,
                        "asset_count": self._submitted,
                        "polygon_shard": job.manifest.output.relative_path,
                    }
                )
                callback: Checkpoint | None = None
                with self._checkpoint_lock:
                    self._completed += 1
                    if self._checkpoint is not None and self._completed >= self._checkpoint_next:
                        callback = self._checkpoint
                        self._checkpoint_next += self._checkpoint_every
                if callback is not None:
                    callback()
                result = await self._builder(
                    job.manifest,
                    job.polygon_path,
                    self._data_root,
                    cache=cache,
                    registry=registry,
                    stop_requested=self._stop_requested,
                    progress=self._progress,
                )
                built += result.status == "built"
                skipped += result.status == "skipped"
                pending += result.status == "pending"
                rows += result.rows
                statuses.update(result.statuses)
                self._progress(
                    {
                        "event": "asset_shard_completed",
                        "asset_index": index,
                        "asset_count": self._submitted,
                        "polygon_shard": result.polygon_shard,
                        "status": result.status,
                        "rows": result.rows,
                        "statuses": result.statuses,
                    }
                )
        finally:
            close = getattr(cache, "close", None)
            if callable(close):
                close()
            aclose = getattr(registry, "aclose", None)
            if callable(aclose):
                await aclose()
        summary = EnrichmentSummary(
            built=built,
            skipped=skipped,
            pending=pending,
            rows=rows,
            statuses=dict(sorted(statuses.items())),
        )
        self._progress(
            {
                "event": "asset_backfill_completed",
                "built": summary.built,
                "skipped": summary.skipped,
                "pending": summary.pending,
                "rows": summary.rows,
                "statuses": summary.status_counts(),
            }
        )
        return summary
