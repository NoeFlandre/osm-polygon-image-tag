import queue
import threading
from pathlib import Path

import pytest

from osm_polygon_image_tag.assets.build_state import AssetBuildResult
from osm_polygon_image_tag.core.manifest import (
    Manifest,
    OutputIdentity,
    RunCounts,
    SourceIdentity,
)
from osm_polygon_image_tag.runtime.enrichment import (
    AssetJob,
    EnrichmentSummary,
    EnrichmentWorker,
)


def _manifest(name: str) -> Manifest:
    return Manifest(
        1,
        2,
        2,
        SourceIdentity(f"{name}.osm.pbf", 1, 1, "a" * 64),
        OutputIdentity(f"data/{name}.parquet", 1, "b" * 64, 1),
        "osmium test",
        RunCounts(1, {}),
    )


def test_worker_processes_stable_deduplicated_queue_with_progress(tmp_path: Path) -> None:
    calls: list[str] = []
    events: list[dict[str, object]] = []

    async def build(manifest: Manifest, path: Path, data_root: Path, **_kwargs: object):
        del manifest, data_root
        calls.append(path.name)
        return AssetBuildResult(
            "built",
            f"data/{path.name}",
            tmp_path / "assets" / f"{path.stem}.assets.parquet",
            tmp_path / "asset-manifests" / f"{path.stem}.json",
            2,
            {"resolved": 2},
        )

    worker = EnrichmentWorker(
        tmp_path,
        builder=build,
        cache_factory=lambda _root: object(),
        registry_factory=lambda: object(),
        stop_requested=lambda: False,
        progress=events.append,
    )
    a = AssetJob(_manifest("a"), tmp_path / "data" / "a.parquet")
    b = AssetJob(_manifest("b"), tmp_path / "data" / "b.parquet")

    worker.start([b, a, a])
    summary = worker.finish()

    assert calls == ["a.parquet", "b.parquet"]
    assert (summary.built, summary.skipped, summary.pending, summary.rows) == (2, 0, 0, 4)
    assert [event["event"] for event in events] == [
        "asset_backfill_started",
        "asset_shard_started",
        "asset_shard_completed",
        "asset_shard_started",
        "asset_shard_completed",
        "asset_backfill_completed",
    ]


def test_worker_prioritizes_missing_asset_artifacts_before_existing(tmp_path: Path) -> None:
    calls: list[str] = []

    async def build(manifest: Manifest, path: Path, data_root: Path, **_kwargs: object):
        del manifest, data_root
        calls.append(path.name)
        return AssetBuildResult(
            "built",
            f"data/{path.name}",
            tmp_path / "assets" / f"{path.stem}.assets.parquet",
            tmp_path / "asset-manifests" / f"{path.stem}.json",
            1,
            {"resolved": 1},
        )

    existing_assets = tmp_path / "assets"
    existing_manifests = tmp_path / "asset-manifests"
    existing_assets.mkdir()
    existing_manifests.mkdir()
    (existing_assets / "existing.assets.parquet").write_bytes(b"asset")
    (existing_manifests / "existing.assets.manifest.json").write_bytes(b"manifest")

    worker = EnrichmentWorker(
        tmp_path,
        builder=build,
        cache_factory=lambda _root: object(),
        registry_factory=lambda: object(),
        stop_requested=lambda: False,
        progress=lambda _event: None,
    )
    worker.start(
        [
            AssetJob(_manifest("existing"), tmp_path / "data" / "existing.parquet"),
            AssetJob(_manifest("missing"), tmp_path / "data" / "missing.parquet"),
        ]
    )

    worker.finish()

    assert calls == ["missing.parquet", "existing.parquet"]


def test_worker_overlaps_main_thread_extraction_and_accepts_new_jobs(tmp_path: Path) -> None:
    asset_started = threading.Event()
    release_asset = threading.Event()
    calls: list[str] = []

    async def build(manifest: Manifest, path: Path, data_root: Path, **_kwargs: object):
        del manifest, data_root
        calls.append(path.name)
        asset_started.set()
        assert release_asset.wait(timeout=2)
        return AssetBuildResult(
            "skipped",
            f"data/{path.name}",
            tmp_path / "assets" / f"{path.stem}.assets.parquet",
            tmp_path / "asset-manifests" / f"{path.stem}.json",
            1,
            {"resolved": 1},
        )

    worker = EnrichmentWorker(
        tmp_path,
        builder=build,
        cache_factory=lambda _root: object(),
        registry_factory=lambda: object(),
        stop_requested=lambda: False,
        progress=lambda _event: None,
    )
    worker.start([AssetJob(_manifest("historical"), tmp_path / "historical.parquet")])
    assert asset_started.wait(timeout=2)

    worker.submit(AssetJob(_manifest("new"), tmp_path / "new.parquet"))
    release_asset.set()
    summary = worker.finish()

    assert calls == ["historical.parquet", "new.parquet"]
    assert summary.skipped == 2


def test_worker_start_returns_while_initial_backfill_is_running(tmp_path: Path) -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    start_returned = threading.Event()
    start_failures: list[BaseException] = []

    async def build(manifest: Manifest, path: Path, data_root: Path, **_kwargs: object):
        del manifest, data_root
        if path.name == "0.parquet":
            first_started.set()
            assert release_first.wait(timeout=2)
        return AssetBuildResult(
            "skipped",
            f"data/{path.name}",
            tmp_path / "assets" / f"{path.stem}.assets.parquet",
            tmp_path / "asset-manifests" / f"{path.stem}.json",
            1,
            {"resolved": 1},
        )

    worker = EnrichmentWorker(
        tmp_path,
        builder=build,
        cache_factory=lambda _root: object(),
        registry_factory=lambda: object(),
        stop_requested=lambda: False,
        progress=lambda _event: None,
    )
    jobs = [AssetJob(_manifest(str(index)), tmp_path / f"{index}.parquet") for index in range(18)]

    def start_worker() -> None:
        try:
            worker.start(jobs)
        except BaseException as error:
            start_failures.append(error)
        finally:
            start_returned.set()

    starter = threading.Thread(target=start_worker, daemon=True)
    starter.start()
    summary: EnrichmentSummary | None = None
    try:
        assert first_started.wait(timeout=2)
        assert start_returned.wait(timeout=1), "start blocked on initial asset backfill"
        assert not start_failures
    finally:
        release_first.set()
        starter.join(timeout=2)
        summary = worker.finish()

    assert summary is not None
    assert summary.skipped == len(jobs)


def test_worker_marks_unstarted_jobs_pending_after_stop(tmp_path: Path) -> None:
    stopped = True

    async def forbidden_builder(*_args: object, **_kwargs: object) -> AssetBuildResult:
        raise AssertionError("stopped worker invoked builder")

    worker = EnrichmentWorker(
        tmp_path,
        builder=forbidden_builder,
        cache_factory=lambda _root: object(),
        registry_factory=lambda: object(),
        stop_requested=lambda: stopped,
        progress=lambda _event: None,
    )
    worker.start([AssetJob(_manifest("a"), tmp_path / "a.parquet")])

    summary = worker.finish()

    assert summary.pending == 1


def test_worker_failure_does_not_deadlock_initial_backfill_start(tmp_path: Path) -> None:
    async def fail(*_args: object, **_kwargs: object) -> AssetBuildResult:
        raise RuntimeError("resolver failed")

    worker = EnrichmentWorker(
        tmp_path,
        builder=fail,
        cache_factory=lambda _root: object(),
        registry_factory=lambda: object(),
        stop_requested=lambda: False,
        progress=lambda _event: None,
    )
    returned = threading.Event()
    failures: list[BaseException] = []

    def start_many() -> None:
        try:
            worker.start(
                AssetJob(_manifest(str(index)), tmp_path / f"{index}.parquet")
                for index in range(32)
            )
        except BaseException as error:
            failures.append(error)
        finally:
            returned.set()

    producer = threading.Thread(target=start_many, daemon=True)
    producer.start()

    assert returned.wait(timeout=2), "producer blocked after enrichment worker failed"
    assert not failures
    with pytest.raises(RuntimeError, match="resolver failed"):
        worker.finish()


def test_worker_runs_bounded_checkpoints_between_asset_shards(tmp_path: Path) -> None:
    first_started = threading.Event()
    release = threading.Event()
    checkpoints: list[str] = []

    async def build(manifest: Manifest, path: Path, data_root: Path, **_kwargs: object):
        del manifest, data_root
        if not first_started.is_set():
            first_started.set()
            assert release.wait(timeout=2)
        return AssetBuildResult(
            "built",
            f"data/{path.name}",
            tmp_path / "assets" / f"{path.stem}.assets.parquet",
            tmp_path / "asset-manifests" / f"{path.stem}.json",
            1,
            {"resolved": 1},
        )

    worker = EnrichmentWorker(
        tmp_path,
        builder=build,
        cache_factory=lambda _root: object(),
        registry_factory=lambda: object(),
        stop_requested=lambda: False,
        progress=lambda _event: None,
    )
    worker.start(
        AssetJob(_manifest(str(index)), tmp_path / f"{index}.parquet") for index in range(5)
    )
    assert first_started.wait(timeout=2)
    worker.enable_checkpoints(lambda: checkpoints.append("publish"), every=2)
    release.set()

    worker.finish()

    assert checkpoints == ["publish", "publish"]


def test_periodic_checkpoint_runs_after_completed_shards(tmp_path: Path) -> None:
    calls: list[str] = []

    async def build(manifest: Manifest, path: Path, data_root: Path, **_kwargs: object):
        del manifest, data_root
        calls.append(path.name)
        return AssetBuildResult(
            "built",
            f"data/{path.name}",
            tmp_path / "assets" / f"{path.stem}.assets.parquet",
            tmp_path / "asset-manifests" / f"{path.stem}.json",
            1,
            {"resolved": 1},
        )

    worker = EnrichmentWorker(
        tmp_path,
        builder=build,
        cache_factory=lambda _root: object(),
        registry_factory=lambda: object(),
        stop_requested=lambda: False,
        progress=lambda _event: None,
    )
    worker.enable_checkpoints(lambda: calls.append("publish"), every=2)
    worker.start(
        AssetJob(_manifest(str(index)), tmp_path / f"{index}.parquet") for index in range(3)
    )

    worker.finish()

    assert calls == ["0.parquet", "1.parquet", "publish", "2.parquet"]


def test_explicit_checkpoint_waits_for_active_shard_and_pauses_next(
    tmp_path: Path,
) -> None:
    first_started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    async def build(manifest: Manifest, path: Path, data_root: Path, **_kwargs: object):
        del manifest, data_root
        calls.append(path.name)
        if len(calls) == 1:
            first_started.set()
            assert release.wait(timeout=2)
        return AssetBuildResult(
            "built",
            f"data/{path.name}",
            tmp_path / "assets" / f"{path.stem}.assets.parquet",
            tmp_path / "asset-manifests" / f"{path.stem}.json",
            1,
            {"resolved": 1},
        )

    worker = EnrichmentWorker(
        tmp_path,
        builder=build,
        cache_factory=lambda _root: object(),
        registry_factory=lambda: object(),
        stop_requested=lambda: False,
        progress=lambda _event: None,
    )
    worker.start(
        AssetJob(_manifest(str(index)), tmp_path / f"{index}.parquet") for index in range(2)
    )
    assert first_started.wait(timeout=2)
    checkpoint_done = threading.Event()
    checkpoint = threading.Thread(
        target=lambda: (
            worker.checkpoint(lambda: calls.append("checkpoint")),
            checkpoint_done.set(),
        ),
        daemon=True,
    )
    checkpoint.start()
    release.set()

    assert checkpoint_done.wait(timeout=2)
    assert calls[:2] == ["0.parquet", "checkpoint"]
    worker.finish()


def test_checkpoint_rejects_dead_worker_failure_before_callback(tmp_path: Path) -> None:
    failed = threading.Event()
    callbacks: list[str] = []

    async def fail_build(
        manifest: Manifest, path: Path, data_root: Path, **_kwargs: object
    ) -> AssetBuildResult:
        del manifest, path, data_root
        failed.set()
        raise RuntimeError("resolver failed")

    worker = EnrichmentWorker(
        tmp_path,
        builder=fail_build,
        cache_factory=lambda _root: object(),
        registry_factory=lambda: object(),
        stop_requested=lambda: False,
        progress=lambda _event: None,
    )
    worker.start([AssetJob(_manifest("failed"), tmp_path / "failed.parquet")])
    assert failed.wait(timeout=2)
    assert worker._thread is not None
    worker._thread.join(timeout=2)

    with pytest.raises(RuntimeError, match="resolver failed"):
        worker.checkpoint(lambda: callbacks.append("published"))

    assert callbacks == []


def test_checkpoint_rechecks_failure_after_worker_termination_race(tmp_path: Path) -> None:
    callbacks: list[str] = []

    worker = EnrichmentWorker(
        tmp_path,
        cache_factory=lambda _root: object(),
        registry_factory=lambda: object(),
        stop_requested=lambda: False,
        progress=lambda _event: None,
    )

    class TerminatingThread:
        def is_alive(self) -> bool:
            worker._error = RuntimeError("late resolver failure")
            return False

    worker._thread = TerminatingThread()  # ty: ignore[invalid-assignment]

    with pytest.raises(RuntimeError, match="late resolver failure"):
        worker.checkpoint(lambda: callbacks.append("published"))

    assert callbacks == []


def test_worker_enqueue_retries_after_a_full_queue(tmp_path: Path) -> None:
    worker = EnrichmentWorker(
        tmp_path,
        cache_factory=lambda _root: object(),
        registry_factory=lambda: object(),
        stop_requested=lambda: False,
        progress=lambda _event: None,
    )
    calls = 0
    original_put = worker._jobs.put

    def flaky_put(job: AssetJob, *, timeout: float) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise queue.Full
        original_put(job, timeout=timeout)

    worker._jobs.put = flaky_put  # ty: ignore[invalid-assignment]
    job = AssetJob(_manifest("queued"), tmp_path / "queued.parquet")

    worker._enqueue_job(job)

    assert calls == 2
    assert worker._jobs.get_nowait() == job

    worker._error = RuntimeError("worker failed")
    with pytest.raises(RuntimeError, match="worker failed"):
        worker._enqueue_job(job)


def test_worker_finish_marker_retries_after_a_full_queue(tmp_path: Path) -> None:
    worker = EnrichmentWorker(
        tmp_path,
        cache_factory=lambda _root: object(),
        registry_factory=lambda: object(),
        stop_requested=lambda: False,
        progress=lambda _event: None,
    )

    class LiveThread:
        def is_alive(self) -> bool:
            return True

    worker._thread = LiveThread()  # ty: ignore[invalid-assignment]
    calls = 0

    def flaky_put(job: AssetJob | None, *, timeout: float) -> None:
        del timeout
        nonlocal calls
        calls += 1
        if calls == 1:
            raise queue.Full
        assert job is None

    worker._jobs.put = flaky_put  # ty: ignore[invalid-assignment]

    worker._send_finish_marker()

    assert calls == 2
    worker._error = RuntimeError("worker failed")
    worker._send_finish_marker()


def test_worker_wait_until_paused_returns_when_thread_is_dead(tmp_path: Path) -> None:
    worker = EnrichmentWorker(
        tmp_path,
        cache_factory=lambda _root: object(),
        registry_factory=lambda: object(),
        stop_requested=lambda: False,
        progress=lambda _event: None,
    )

    class DeadThread:
        def is_alive(self) -> bool:
            return False

    worker._thread = DeadThread()  # ty: ignore[invalid-assignment]

    worker._wait_until_paused()
    worker._error = RuntimeError("worker failed")
    with pytest.raises(RuntimeError, match="worker failed"):
        worker._wait_until_paused()
