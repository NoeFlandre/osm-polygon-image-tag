import signal
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from osm_polygon_image_tag.artifacts.publication import PublicationResult
from osm_polygon_image_tag.artifacts.reporting import MetadataResult
from osm_polygon_image_tag.core.config import PipelinePaths
from osm_polygon_image_tag.ingest.discovery import PbfSource
from osm_polygon_image_tag.runtime.enrichment import EnrichmentSummary
from osm_polygon_image_tag.runtime.orchestrator import StopToken, graceful_stop_signals, run_all
from osm_polygon_image_tag.runtime.pipeline import BuildResult


def _result(source: str) -> BuildResult:
    return BuildResult(
        status="built",
        source_pbf=source,
        output_path=Path(f"/output/{source}.parquet"),
        manifest_path=Path(f"/output/{source}.json"),
        accepted_rows=1,
        rejections={},
    )


def _metadata(root: Path) -> MetadataResult:
    return MetadataResult(root / "statistics.json", root / "README.md")


def test_run_all_uses_deterministic_source_order(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    (source / "z.osm.pbf").write_bytes(b"z")
    (source / "a.osm.pbf").write_bytes(b"a")
    paths = PipelinePaths.build(source_root=source, data_root=tmp_path / "generated")
    seen: list[str] = []

    def build(pbf: PbfSource, _paths: PipelinePaths) -> BuildResult:
        name = pbf.relative_path.as_posix()
        seen.append(name)
        return _result(name)

    summary = run_all(paths, build=build, metadata_builder=_metadata)

    assert seen == ["a.osm.pbf", "z.osm.pbf"]
    assert summary.stopped is False
    assert summary.built == 2
    assert summary.skipped == 0


def test_run_all_removes_abandoned_pipeline_temps_before_resuming(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    (source / "a.osm.pbf").write_bytes(b"a")
    data_root = tmp_path / "generated"
    data_dir = data_root / "data"
    manifests_dir = data_root / "manifests"
    temporary_dir = data_root / "tmp"
    data_dir.mkdir(parents=True)
    manifests_dir.mkdir()
    temporary_dir.mkdir()
    data_temp = data_dir / ".region.parquet.k_wyod3b.tmp"
    manifest_temp = manifests_dir / ".region.manifest.json.k_wyod3b.tmp"
    tag_store_temp = temporary_dir / "tag-store-ziwk538k.sqlite"
    for path in (data_temp, manifest_temp, tag_store_temp):
        path.write_bytes(b"abandoned")
    unknown = temporary_dir / "keep-me.tmp"
    unknown.write_bytes(b"unknown")
    paths = PipelinePaths.build(source_root=source, data_root=data_root)

    def build(pbf: PbfSource, _paths: PipelinePaths) -> BuildResult:
        assert pbf.relative_path.as_posix() == "a.osm.pbf"
        assert not data_temp.exists()
        assert not manifest_temp.exists()
        assert not tag_store_temp.exists()
        assert unknown.exists()
        return _result("a.osm.pbf")

    run_all(paths, build=build, metadata_builder=_metadata)

    assert unknown.exists()


def test_stop_token_prevents_starting_the_next_pbf(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    (source / "a.osm.pbf").write_bytes(b"a")
    (source / "b.osm.pbf").write_bytes(b"b")
    paths = PipelinePaths.build(source_root=source, data_root=tmp_path / "generated")
    token = StopToken()
    seen: list[str] = []

    def build(pbf: PbfSource, _paths: PipelinePaths) -> BuildResult:
        name = pbf.relative_path.as_posix()
        seen.append(name)
        token.request()
        return _result(name)

    summary = run_all(
        paths,
        build=build,
        stop_token=token,
        metadata_builder=_metadata,
    )

    assert seen == ["a.osm.pbf"]
    assert summary.stopped is True
    assert summary.processed == 1


def test_signal_handler_requests_stop_without_raising() -> None:
    token = StopToken()

    with graceful_stop_signals(token):
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        cast(Callable[[int, object], object], handler)(signal.SIGINT, None)

    assert token.requested


def test_run_all_publishes_after_each_completed_pbf(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    (source / "a.osm.pbf").write_bytes(b"a")
    (source / "b.osm.pbf").write_bytes(b"b")
    paths = PipelinePaths.build(source_root=source, data_root=tmp_path / "generated")
    events: list[str] = []

    def build(pbf: PbfSource, _paths: PipelinePaths) -> BuildResult:
        events.append(f"build:{pbf.relative_path}")
        return _result(pbf.relative_path.as_posix())

    def metadata(root: Path) -> MetadataResult:
        events.append("metadata")
        return _metadata(root)

    def publish(_root: Path) -> PublicationResult:
        events.append("publish")
        return PublicationResult("published", "commit", 1)

    run_all(paths, build=build, metadata_builder=metadata, publisher=publish)

    assert events == [
        "build:a.osm.pbf",
        "metadata",
        "publish",
        "build:b.osm.pbf",
        "metadata",
        "publish",
    ]


def test_resume_defers_metadata_and_publication_until_new_work_is_built(
    tmp_path: Path,
) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    for name in ("a.osm.pbf", "b.osm.pbf", "c.osm.pbf"):
        (source / name).write_bytes(name.encode())
    paths = PipelinePaths.build(source_root=source, data_root=tmp_path / "generated")
    events: list[str] = []

    def build(pbf: PbfSource, _paths: PipelinePaths) -> BuildResult:
        name = pbf.relative_path.as_posix()
        events.append(f"build:{name}")
        result = _result(name)
        if name != "c.osm.pbf":
            return BuildResult(
                status="skipped",
                source_pbf=result.source_pbf,
                output_path=result.output_path,
                manifest_path=result.manifest_path,
                accepted_rows=result.accepted_rows,
                rejections=result.rejections,
            )
        return result

    def metadata(root: Path) -> MetadataResult:
        events.append("metadata")
        return _metadata(root)

    def publish(_root: Path) -> PublicationResult:
        events.append("publish")
        return PublicationResult("published", "commit", 1)

    run_all(
        paths,
        build=build,
        metadata_builder=metadata,
        publisher=publish,
    )

    assert events == [
        "build:a.osm.pbf",
        "build:b.osm.pbf",
        "build:c.osm.pbf",
        "metadata",
        "publish",
    ]


def test_run_all_reports_ordered_per_pbf_progress(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    (source / "region.osm.pbf").write_bytes(b"source")
    paths = PipelinePaths.build(source_root=source, data_root=tmp_path / "generated")
    events: list[dict[str, object]] = []

    run_all(
        paths,
        build=lambda pbf, _paths: _result(pbf.relative_path.as_posix()),
        metadata_builder=_metadata,
        publisher=lambda _root: PublicationResult("published", "abc", 4),
        progress=events.append,
    )

    assert [event["event"] for event in events] == [
        "run_started",
        "pbf_started",
        "pbf_completed",
        "metadata_started",
        "metadata_completed",
        "publication_started",
        "publication_completed",
        "run_completed",
    ]
    assert events[0] == {
        "event": "run_started",
        "pbf_count": 1,
        "pbf_bytes": 6,
    }
    assert events[1]["source_pbf"] == "region.osm.pbf"
    assert events[1]["pbf_index"] == 1
    assert events[1]["pbf_count"] == 1
    assert events[2]["status"] == "built"
    assert events[-1]["processed"] == 1


def test_all_skipped_polygon_run_flushes_new_asset_backfill_once(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    (source / "region.osm.pbf").write_bytes(b"source")
    paths = PipelinePaths.build(source_root=source, data_root=tmp_path / "generated")
    events: list[str] = []

    class Worker:
        def start(self, initial_jobs: object) -> None:
            del initial_jobs
            events.append("asset-start")

        def submit(self, job: object) -> bool:
            del job
            events.append("asset-submit")
            return True

        def finish(self) -> EnrichmentSummary:
            events.append("asset-finish")
            return EnrichmentSummary(built=1, rows=2, statuses={"resolved": 2})

        def enable_checkpoints(self, callback: Callable[[], None], *, every: int) -> None:
            del callback, every

        def checkpoint(self, callback: Callable[[], None]) -> None:
            callback()

    skipped = _result("region.osm.pbf")
    skipped = BuildResult(
        "skipped",
        skipped.source_pbf,
        skipped.output_path,
        skipped.manifest_path,
        skipped.accepted_rows,
        skipped.rejections,
    )

    summary = run_all(
        paths,
        build=lambda _pbf, _paths: skipped,
        metadata_builder=lambda root: events.append("metadata") or _metadata(root),
        publisher=lambda _root: events.append("publish")
        or PublicationResult("published", "abc", 1),
        enrichment_worker=Worker(),
    )

    assert events == ["asset-start", "asset-finish", "metadata", "publish"]
    assert summary.enrichment.built == 1
    assert summary.enrichment.rows == 2


def test_extraction_failure_stops_and_closes_enrichment_worker(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    (source / "region.osm.pbf").write_bytes(b"source")
    paths = PipelinePaths.build(source_root=source, data_root=tmp_path / "generated")
    token = StopToken()
    events: list[str] = []

    class Worker:
        def start(self, initial_jobs: object) -> None:
            del initial_jobs
            events.append("start")

        def submit(self, job: object) -> bool:
            del job
            return True

        def finish(self) -> EnrichmentSummary:
            events.append("finish")
            return EnrichmentSummary()

        def enable_checkpoints(self, callback: Callable[[], None], *, every: int) -> None:
            del callback, every

        def checkpoint(self, callback: Callable[[], None]) -> None:
            callback()

    def fail(_pbf: PbfSource, _paths: PipelinePaths) -> BuildResult:
        raise RuntimeError("extraction failed")

    with pytest.raises(RuntimeError, match="extraction failed"):
        run_all(
            paths,
            build=fail,
            stop_token=token,
            enrichment_worker=Worker(),
        )

    assert token.requested
    assert events == ["start", "finish"]


def test_enriched_run_publishes_periodic_asset_checkpoints(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    paths = PipelinePaths.build(source_root=source, data_root=tmp_path / "generated")
    events: list[str] = []

    class Worker:
        def start(self, initial_jobs: object) -> None:
            del initial_jobs

        def submit(self, job: object) -> bool:
            del job
            return True

        def enable_checkpoints(self, callback: Callable[[], None], *, every: int) -> None:
            assert every == 1
            callback()
            callback()

        def checkpoint(self, callback: Callable[[], None]) -> None:
            callback()

        def finish(self) -> EnrichmentSummary:
            return EnrichmentSummary(built=50)

    run_all(
        paths,
        enrichment_worker=Worker(),
        metadata_builder=lambda root: events.append("metadata") or _metadata(root),
        publisher=lambda _root: events.append("publish")
        or PublicationResult("published", "abc", 1),
    )

    assert events == [
        "metadata",
        "publish",
        "metadata",
        "publish",
        "metadata",
        "publish",
    ]


def test_asset_publication_checkpoints_are_enabled_before_pbf_scan(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    (source / "region.osm.pbf").write_bytes(b"source")
    paths = PipelinePaths.build(source_root=source, data_root=tmp_path / "generated")
    events: list[str] = []

    class Worker:
        def enable_checkpoints(self, callback: Callable[[], None], *, every: int) -> None:
            del callback
            assert every == 1
            events.append("checkpoints-enabled")

        def start(self, initial_jobs: object) -> None:
            del initial_jobs
            events.append("asset-start")

        def submit(self, job: object) -> bool:
            del job
            return True

        def checkpoint(self, callback: Callable[[], None]) -> None:
            callback()

        def finish(self) -> EnrichmentSummary:
            return EnrichmentSummary()

    def build(pbf: PbfSource, _paths: PipelinePaths) -> BuildResult:
        events.append(f"scan:{pbf.relative_path}")
        return BuildResult(
            "skipped",
            pbf.relative_path.as_posix(),
            Path("/output/region.parquet"),
            Path("/output/region.json"),
            0,
            {},
        )

    run_all(
        paths,
        build=build,
        enrichment_worker=Worker(),
        metadata_builder=_metadata,
        publisher=lambda _root: PublicationResult("unchanged", "", 0),
    )

    assert events[:3] == [
        "checkpoints-enabled",
        "asset-start",
        "scan:region.osm.pbf",
    ]


def test_checkpoint_publication_race_with_active_pbf_build(tmp_path: Path) -> None:
    """
    Regression test for the checkpoint/publication race.

    The race occurs when:
    1. Main thread is building a PBF (creates temporary files in data/ and tmp/)
    2. The enrichment worker's periodic checkpoint callback invokes publication
       while those temporary files still exist
    3. publication_inventory correctly rejects the temporary files as unexpected

    With the per-run refresh_lock, the periodic checkpoint callback blocks until
    the build releases the lock. Without it, the callback runs while temp files
    exist and publication_inventory raises.

    The test models the race deterministically using threading.Event and a
    threading.Barrier (no time.sleep, no arbitrary polling):
    A. The fake build creates representative owned temp files and blocks.
    B. The build signals temp files are present.
    C. The periodic checkpoint callback is invoked from a separate thread.
    D. A barrier synchronizes the callback's start with the test's release.
    E. The publish spy signals when it is invoked and blocks until the test
       allows it to call publication_inventory. This ensures the inventory
       check runs while temp files are still present (or after the build
       has finalized them, depending on lock presence).
    F. With the lock, publish is never reached because the callback blocks on
       refresh_lock; without the lock, publish runs and observes temp files.
    G. Publication inventory must not observe the temporary files after the fix.
    """
    import threading
    from pathlib import Path

    from osm_polygon_image_tag.artifacts.publication import PublicationResult
    from osm_polygon_image_tag.artifacts.publication_inventory import publication_inventory
    from osm_polygon_image_tag.artifacts.reporting import generate_metadata
    from osm_polygon_image_tag.artifacts.storage import write_geoparquet
    from osm_polygon_image_tag.core.config import PipelinePaths
    from osm_polygon_image_tag.core.manifest import (
        DATASET_SCHEMA_VERSION,
        PROCESSING_CONTRACT_VERSION,
        Manifest,
        OutputIdentity,
        RunCounts,
        SourceIdentity,
        file_sha256,
        write_manifest,
    )
    from osm_polygon_image_tag.ingest.discovery import PbfSource
    from osm_polygon_image_tag.runtime.orchestrator import run_all
    from osm_polygon_image_tag.runtime.pipeline import BuildResult

    source = tmp_path / "raw"
    source.mkdir()
    (source / "region.osm.pbf").write_bytes(b"source")
    data_root = tmp_path / "generated"
    paths = PipelinePaths.build(source_root=source, data_root=data_root)

    # Build a minimal valid dataset so publication_inventory succeeds when clean
    data_dir = data_root / "data"
    manifests_dir = data_root / "manifests"
    temporary_dir = data_root / "tmp"
    data_dir.mkdir(parents=True)
    manifests_dir.mkdir()
    temporary_dir.mkdir()

    data_file = data_dir / "region.parquet"
    write_geoparquet([], data_file)
    manifest = Manifest(
        manifest_schema_version=1,
        processing_contract_version=PROCESSING_CONTRACT_VERSION,
        dataset_schema_version=DATASET_SCHEMA_VERSION,
        source=SourceIdentity("region.osm.pbf", 1, 1, "a" * 64),
        output=OutputIdentity(
            "data/region.parquet",
            data_file.stat().st_size,
            file_sha256(data_file),
            0,
        ),
        osmium_version="test",
        counts=RunCounts(0, {}),
    )
    write_manifest(manifest, manifests_dir / "region.manifest.json")
    generate_metadata(data_root)

    # Deterministic synchronization primitives (no time.sleep, no polling)
    build_started = threading.Event()
    build_can_finish = threading.Event()
    publish_called = threading.Event()
    publish_can_proceed = threading.Event()
    callback_completed = threading.Event()
    race_observed = threading.Event()
    # Barrier: ensures the callback thread is executing before the test
    # proceeds to observe the race or release the build.
    callback_started = threading.Barrier(2)

    pipeline_exception: list[BaseException] = []
    callback_exception: list[BaseException] = []

    def build(pbf: PbfSource, _paths: PipelinePaths) -> BuildResult:
        # Create representative owned temporary files like the real PBF build
        data_temp = data_dir / ".region-latest-abcdef12.parquet.xyz789.tmp"
        tag_store_temp = temporary_dir / "tag-store-abc123.sqlite"
        data_temp.write_bytes(b"partial parquet write")
        tag_store_temp.write_bytes(b"active tag store")
        build_started.set()

        # Block until the test releases us
        assert build_can_finish.wait(timeout=5.0), "Build timed out waiting for release"

        # Finalize: remove temporary files (real build does atomic rename)
        data_temp.unlink(missing_ok=True)
        tag_store_temp.unlink(missing_ok=True)
        return BuildResult(
            status="built",
            source_pbf=pbf.relative_path.as_posix(),
            output_path=data_dir / "region.parquet",
            manifest_path=manifests_dir / "region.manifest.json",
            accepted_rows=10,
            rejections={},
        )

    def metadata(root: Path) -> MetadataResult:
        # Fast no-op metadata builder so publish is reached immediately
        # after the callback acquires the lock (or immediately without it).
        del root
        return MetadataResult(data_root / "statistics.json", data_root / "README.md")

    def publish(root: Path) -> PublicationResult:
        # Signal that publish was reached and block until the test allows
        # the inventory check. This guarantees the test can observe whether
        # temp files are present at the moment publication_inventory runs.
        publish_called.set()
        assert publish_can_proceed.wait(timeout=5.0), (
            "publish spy timed out waiting for test to allow proceed"
        )
        try:
            inventory = publication_inventory(root)
        except Exception as exc:
            msg = str(exc).lower()
            if "unexpected" in msg and (".tmp" in msg or "tag-store" in msg):
                race_observed.set()
            raise
        return PublicationResult("published", "commit", len(inventory))

    # Fake EnrichmentController that captures the periodic checkpoint callback
    class _FakeWorker:
        def __init__(self) -> None:
            self._periodic_callback: Callable[[], None] | None = None

        def enable_checkpoints(self, callback: Callable[[], None], *, every: int) -> None:
            assert every == 1
            self._periodic_callback = callback

        def start(self, initial_jobs: object) -> None:
            del initial_jobs

        def submit(self, job: object) -> bool:
            del job
            return True

        def checkpoint(self, callback: Callable[[], None]) -> None:
            callback()

        def finish(self) -> EnrichmentSummary:
            return EnrichmentSummary()

    worker = _FakeWorker()

    # Run the pipeline in a separate thread so we can coordinate with it
    def run_pipeline() -> None:
        try:
            run_all(
                paths,
                build=build,
                metadata_builder=metadata,
                publisher=publish,
                enrichment_worker=worker,
            )
        except BaseException as exc:
            pipeline_exception.append(exc)

    pipeline_thread = threading.Thread(target=run_pipeline, daemon=True)
    pipeline_thread.start()
    try:
        # A. Wait for the build to start and create temp files
        assert build_started.wait(timeout=2.0), "Build did not start in time"

        # Verify temp files are present (the precondition for the race)
        assert (data_dir / ".region-latest-abcdef12.parquet.xyz789.tmp").exists()
        assert (temporary_dir / "tag-store-abc123.sqlite").exists()

        # C. Invoke the periodic checkpoint callback from a separate thread.
        callback = worker._periodic_callback
        assert callback is not None, "Periodic checkpoint callback was not registered"

        def invoke_callback() -> None:
            # Barrier: signal that the callback thread is executing and wait
            # for the test thread to also reach this point. This guarantees
            # the test proceeds only after the callback has started.
            callback_started.wait(timeout=5.0)
            try:
                callback()
            except BaseException as exc:
                callback_exception.append(exc)
            finally:
                callback_completed.set()

        callback_thread = threading.Thread(target=invoke_callback, daemon=True)
        callback_thread.start()

        # D. Barrier: synchronize with the callback thread so we know it is
        # executing (either blocked on the lock or running publish).
        callback_started.wait(timeout=2.0)

        # E/F. Wait for publish to be called. If the callback is blocked on
        # the refresh_lock (with the fix), publish is never reached and this
        # wait times out — the callback is correctly serialized. If the lock
        # is absent, publish runs immediately and signals here.
        publish_reached = publish_called.wait(timeout=1.0)
        if publish_reached:
            # Without the lock: publish was reached while temp files exist.
            # The callback thread is now blocked on publish_can_proceed.
            # Verify temp files are still present, then allow publish to
            # proceed so the inventory check observes them.
            assert (data_dir / ".region-latest-abcdef12.parquet.xyz789.tmp").exists(), (
                "Temp files were removed before publish could observe them"
            )
            assert (temporary_dir / "tag-store-abc123.sqlite").exists()
            # Allow the inventory check to run while temp files are present
            publish_can_proceed.set()
            # Wait for the callback to complete (it should fail)
            assert callback_completed.wait(timeout=5.0), "Callback did not complete"
            callback_thread.join(timeout=2.0)
            # The race must have been observed
            assert race_observed.is_set(), (
                "Race not observed: publication_inventory should have rejected "
                "temp files but didn't."
            )
            # Clean up: release the build so the pipeline can finish
            build_can_finish.set()
        else:
            # With the lock: the callback is blocked on refresh_lock and
            # publish was never reached. Release the build so the lock
            # becomes available, then wait for the callback to complete.
            build_can_finish.set()
            assert publish_called.wait(timeout=5.0), (
                "publish was not called after build released lock"
            )
            # Now publish is blocked on publish_can_proceed. Temp files are
            # gone (build already finalized). Allow publish to proceed.
            assert not (data_dir / ".region-latest-abcdef12.parquet.xyz789.tmp").exists(), (
                "Temp files still present after build release"
            )
            assert not (temporary_dir / "tag-store-abc123.sqlite").exists()
            publish_can_proceed.set()
            assert callback_completed.wait(timeout=5.0), (
                "Callback did not complete after lock release"
            )
            callback_thread.join(timeout=2.0)
            # No race should have been observed
            assert not race_observed.is_set(), (
                "Race was observed: publication_inventory saw temp files."
            )

        assert not callback_thread.is_alive(), "Callback thread did not terminate"

        # Wait for the pipeline to complete
        pipeline_thread.join(timeout=5.0)
        assert not pipeline_thread.is_alive(), "Pipeline thread did not terminate"

        assert not callback_exception, (
            f"Callback raised an unexpected exception: {callback_exception[0]!r}"
        )
        assert not pipeline_exception, (
            f"Pipeline raised an unexpected exception: {pipeline_exception[0]!r}"
        )
    finally:
        # Ensure all threads are cleaned up even if an assertion fails
        build_can_finish.set()
        publish_can_proceed.set()
        if pipeline_thread.is_alive():
            pipeline_thread.join(timeout=5.0)
