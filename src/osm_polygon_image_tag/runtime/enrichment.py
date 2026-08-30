import asyncio
import queue
import threading
from collections import Counter, deque
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import cast

from osm_polygon_image_tag.assets.build_state import AssetBuildResult, asset_paths
from osm_polygon_image_tag.assets.builder import build_asset_shard
from osm_polygon_image_tag.core.progress import Progress
from osm_polygon_image_tag.runtime.enrichment_types import AssetJob, EnrichmentSummary

AssetBuilder = Callable[..., Awaitable[AssetBuildResult]]
Checkpoint = Callable[[], None]


def _asset_artifacts_present(job: AssetJob, data_root: Path) -> bool:
    output, manifest = asset_paths(job.polygon_path, data_root)
    return all(path.is_file() and not path.is_symlink() for path in (output, manifest))


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
        self._initial_jobs: deque[AssetJob] = deque()
        self._prefer_initial = True
        self._finish_marker_seen = False
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
        ordered = sorted(
            initial_jobs,
            key=lambda job: (
                _asset_artifacts_present(job, self._data_root),
                job.polygon_path.as_posix(),
            ),
        )
        for job in ordered:
            if self._register(job):
                self._initial_jobs.append(job)
        self._thread = threading.Thread(
            target=self._thread_main,
            name="image-asset-enrichment",
            daemon=False,
        )
        self._thread.start()

    def submit(self, job: AssetJob) -> bool:
        if not self._register(job):
            return False
        self._enqueue_job(job)
        return True

    def _enqueue_job(self, job: AssetJob) -> None:
        while True:
            if self._error is not None:
                raise self._error
            try:
                self._jobs.put(job, timeout=0.1)
                break
            except queue.Full:
                continue

    def _register(self, job: AssetJob) -> bool:
        identity = job.polygon_path.resolve().as_posix()
        with self._seen_lock:
            if identity in self._seen:
                return False
            self._seen.add(identity)
            self._submitted += 1
        return True

    def enable_checkpoints(self, callback: Checkpoint, *, every: int) -> None:
        if every <= 0:
            raise ValueError("checkpoint interval must be positive")
        with self._checkpoint_lock:
            self._checkpoint = callback
            self._checkpoint_every = every
            self._checkpoint_next = self._completed + every

    def checkpoint(self, callback: Checkpoint) -> None:
        self._ensure_started()
        self._raise_worker_error()
        if not self._pause_for_checkpoint():
            callback()
            return
        try:
            callback()
        finally:
            self._pause_requested.clear()
            self._resume.set()

    def finish(self) -> EnrichmentSummary:
        self._ensure_started()
        assert self._thread is not None
        self._send_finish_marker()
        self._thread.join()
        self._raise_worker_error()
        return self._summary

    def _ensure_started(self) -> None:
        if self._thread is None:
            raise RuntimeError("enrichment worker was not started")

    def _raise_worker_error(self) -> None:
        if self._error is not None:
            raise self._error

    def _pause_for_checkpoint(self) -> bool:
        assert self._thread is not None
        if not self._thread.is_alive():
            self._raise_worker_error()
            return False
        self._resume.clear()
        self._pause_requested.set()
        self._wait_until_paused()
        return True

    def _wait_until_paused(self) -> None:
        thread = cast(threading.Thread, self._thread)
        while thread.is_alive() and not self._paused.wait(timeout=0.1):
            continue
        if self._error is not None:
            self._pause_requested.clear()
            self._resume.set()
            raise self._error

    def _send_finish_marker(self) -> None:
        thread = cast(threading.Thread, self._thread)
        while thread.is_alive() and self._error is None:
            try:
                self._jobs.put(None, timeout=0.1)
                break
            except queue.Full:
                continue

    def _thread_main(self) -> None:
        try:
            self._summary = asyncio.run(self._run())
        except BaseException as error:
            self._error = error

    async def _process_job(
        self,
        job: AssetJob,
        index: int,
        *,
        cache: object,
        registry: object,
    ) -> AssetBuildResult:
        self._progress(
            {
                "event": "asset_shard_started",
                "asset_index": index,
                "asset_count": self._submitted,
                "polygon_shard": job.manifest.output.relative_path,
            }
        )
        result = await self._builder(
            job.manifest,
            job.polygon_path,
            self._data_root,
            cache=cache,
            registry=registry,
            stop_requested=self._stop_requested,
            progress=self._progress,
        )
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
        return result

    def _checkpoint_after(self, result: AssetBuildResult) -> Checkpoint | None:
        with self._checkpoint_lock:
            if result.status != "built":
                return None
            self._completed += 1
            if self._checkpoint is None or self._completed < self._checkpoint_next:
                return None
            callback = self._checkpoint
            self._checkpoint_next += self._checkpoint_every
            return callback

    async def _run(self) -> EnrichmentSummary:
        cache = self._cache_factory(self._data_root)
        registry = self._registry_factory()
        self._progress(
            {
                "event": "asset_backfill_started",
                "asset_count": self._submitted,
            }
        )
        try:
            summary = await self._run_jobs(cache, registry)
        finally:
            close = getattr(cache, "close", None)
            if callable(close):
                close()
            aclose = getattr(registry, "aclose", None)
            if callable(aclose):
                await aclose()
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

    async def _run_jobs(self, cache: object, registry: object) -> EnrichmentSummary:
        built = skipped = pending = rows = index = 0
        statuses: Counter[str] = Counter()
        while True:
            self._pause_if_requested()
            job = self._next_available_job()
            if job is None:
                break
            index += 1
            if self._stop_requested():
                pending += 1
                continue
            result = await self._process_job(job, index, cache=cache, registry=registry)
            built += result.status == "built"
            skipped += result.status == "skipped"
            pending += result.status == "pending"
            rows += result.rows
            statuses.update(result.statuses)
            callback = self._checkpoint_after(result)
            if callback is not None:
                callback()
        return EnrichmentSummary(
            built=built,
            skipped=skipped,
            pending=pending,
            rows=rows,
            statuses=dict(sorted(statuses.items())),
        )

    def _pause_if_requested(self) -> None:
        if not self._pause_requested.is_set():
            return
        self._paused.set()
        self._resume.wait()
        self._paused.clear()

    def _next_available_job(self) -> AssetJob | None:
        while True:
            job = self._next_job()
            if job is not _NO_JOB:
                return cast(AssetJob | None, job)

    def _next_job(self) -> AssetJob | None | object:
        if self._initial_jobs:
            return self._next_initial_job()
        if self._finish_marker_seen:
            return None
        return self._next_queued_job()

    def _next_initial_job(self) -> AssetJob | None | object:
        if self._prefer_initial:
            self._prefer_initial = False
            return self._initial_jobs.popleft()
        try:
            job = self._jobs.get_nowait()
        except queue.Empty:
            self._prefer_initial = True
            return self._initial_jobs.popleft()
        if job is None and self._initial_jobs:
            self._finish_marker_seen = True
            self._prefer_initial = False
            return self._initial_jobs.popleft()
        self._prefer_initial = True
        return job

    def _next_queued_job(self) -> AssetJob | None | object:
        try:
            return self._jobs.get(timeout=0.1)
        except queue.Empty:
            return _NO_JOB


_NO_JOB = object()
