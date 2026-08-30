import signal
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from osm_polygon_image_tag.artifacts.publication import PublicationResult
from osm_polygon_image_tag.artifacts.publication_inventory import publication_inventory
from osm_polygon_image_tag.artifacts.reporting import MetadataResult, generate_metadata
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
from osm_polygon_image_tag.runtime.enrichment import AssetJob, EnrichmentSummary
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


class FakeEnrichmentWorker:
    """Small configurable worker double shared by orchestrator tests."""

    def __init__(
        self,
        *,
        events: list[str] | None = None,
        summary: EnrichmentSummary | None = None,
        checkpoint_runs: int = 0,
        checkpoint_enable_event: str | None = None,
        start_event: str = "asset-start",
        finish_event: str = "asset-finish",
    ) -> None:
        self._events = events
        self._summary = summary if summary is not None else EnrichmentSummary()
        self._checkpoint_runs = checkpoint_runs
        self._checkpoint_enable_event = checkpoint_enable_event
        self._start_event = start_event
        self._finish_event = finish_event
        self.periodic_callback: Callable[[], None] | None = None

    def start(self, initial_jobs: Iterable[AssetJob]) -> None:
        del initial_jobs
        if self._events is not None:
            self._events.append(self._start_event)

    def submit(self, job: AssetJob) -> bool:
        del job
        return True

    def finish(self) -> EnrichmentSummary:
        if self._events is not None:
            self._events.append(self._finish_event)
        return self._summary

    def enable_checkpoints(self, callback: Callable[[], None], *, every: int) -> None:
        assert every == 1
        self.periodic_callback = callback
        if self._events is not None and self._checkpoint_enable_event is not None:
            self._events.append(self._checkpoint_enable_event)
        for _ in range(self._checkpoint_runs):
            callback()

    def checkpoint(self, callback: Callable[[], None]) -> None:
        callback()


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
    assets_dir = data_root / "assets"
    manifests_dir = data_root / "manifests"
    public_dir = data_root / "public"
    temporary_dir = data_root / "tmp"
    data_dir.mkdir(parents=True)
    assets_dir.mkdir()
    manifests_dir.mkdir()
    public_dir.mkdir()
    temporary_dir.mkdir()
    data_temp = data_dir / ".region.parquet.k_wyod3b.tmp"
    manifest_temp = manifests_dir / ".region.manifest.json.k_wyod3b.tmp"
    tag_store_temp = temporary_dir / "tag-store-ziwk538k.sqlite"
    legacy_public_asset_temp = temporary_dir / ".public-assets.ziwk538k.sqlite"
    public_asset_checkpoint = temporary_dir / ".public-assets.sqlite"
    asset_sort_temp = assets_dir / ".asset-sort.4lhdb7ue.sqlite"
    asset_sort_journal = assets_dir / ".asset-sort.4lhdb7ue.sqlite-journal"
    public_polygon_temp = public_dir / ".polygons.parquet.abc12345.tmp"
    public_manifest_temp = public_dir / ".public-manifest.abc12345.tmp"
    for path in (
        data_temp,
        manifest_temp,
        tag_store_temp,
        legacy_public_asset_temp,
        asset_sort_temp,
        asset_sort_journal,
        public_polygon_temp,
        public_manifest_temp,
    ):
        path.write_bytes(b"abandoned")
    unknown = temporary_dir / "keep-me.tmp"
    unknown.write_bytes(b"unknown")
    public_asset_checkpoint.write_bytes(b"resume")
    unknown_asset = assets_dir / ".asset-sort.not-owned.sqlite"
    unknown_asset.write_bytes(b"unknown")
    paths = PipelinePaths.build(source_root=source, data_root=data_root)

    def build(pbf: PbfSource, _paths: PipelinePaths) -> BuildResult:
        assert pbf.relative_path.as_posix() == "a.osm.pbf"
        assert not data_temp.exists()
        assert not manifest_temp.exists()
        assert not tag_store_temp.exists()
        assert not legacy_public_asset_temp.exists()
        assert not asset_sort_temp.exists()
        assert not asset_sort_journal.exists()
        assert not public_polygon_temp.exists()
        assert not public_manifest_temp.exists()
        assert public_asset_checkpoint.exists()
        assert unknown.exists()
        assert unknown_asset.exists()
        return _result("a.osm.pbf")

    run_all(paths, build=build, metadata_builder=_metadata)

    assert unknown.exists()
    assert unknown_asset.exists()


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

    worker = FakeEnrichmentWorker(
        events=events,
        summary=EnrichmentSummary(built=1, rows=2, statuses={"resolved": 2}),
    )

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
        enrichment_worker=worker,
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

    worker = FakeEnrichmentWorker(events=events, start_event="start", finish_event="finish")

    def fail(_pbf: PbfSource, _paths: PipelinePaths) -> BuildResult:
        raise RuntimeError("extraction failed")

    with pytest.raises(RuntimeError, match="extraction failed"):
        run_all(
            paths,
            build=fail,
            stop_token=token,
            enrichment_worker=worker,
        )

    assert token.requested
    assert events == ["start", "finish"]


def test_enriched_run_publishes_periodic_asset_checkpoints(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    paths = PipelinePaths.build(source_root=source, data_root=tmp_path / "generated")
    events: list[str] = []

    worker = FakeEnrichmentWorker(
        summary=EnrichmentSummary(built=50),
        checkpoint_runs=2,
    )

    run_all(
        paths,
        enrichment_worker=worker,
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

    worker = FakeEnrichmentWorker(
        events=events,
        checkpoint_enable_event="checkpoints-enabled",
    )

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
        enrichment_worker=worker,
        metadata_builder=_metadata,
        publisher=lambda _root: PublicationResult("unchanged", "", 0),
    )

    assert events[:3] == [
        "checkpoints-enabled",
        "asset-start",
        "scan:region.osm.pbf",
    ]


@dataclass(slots=True)
class _CheckpointRaceFixture:
    paths: PipelinePaths
    data_root: Path
    data_dir: Path
    manifests_dir: Path
    temporary_dir: Path
    worker: FakeEnrichmentWorker
    build: Callable[[PbfSource, PipelinePaths], BuildResult]
    metadata: Callable[[Path], MetadataResult]
    publish: Callable[[Path], PublicationResult]
    data_temp: Path
    tag_store_temp: Path
    build_started: threading.Event
    build_can_finish: threading.Event
    publish_called: threading.Event
    publish_can_proceed: threading.Event
    callback_completed: threading.Event
    race_observed: threading.Event
    callback_started: threading.Barrier


def _checkpoint_race_fixture(tmp_path: Path) -> _CheckpointRaceFixture:
    source = tmp_path / "raw"
    source.mkdir()
    (source / "region.osm.pbf").write_bytes(b"source")
    data_root = tmp_path / "generated"
    paths = PipelinePaths.build(source_root=source, data_root=data_root)

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

    build_started = threading.Event()
    build_can_finish = threading.Event()
    publish_called = threading.Event()
    publish_can_proceed = threading.Event()
    callback_completed = threading.Event()
    race_observed = threading.Event()
    callback_started = threading.Barrier(2)
    data_temp = data_dir / ".region-latest-abcdef12.parquet.xyz789.tmp"
    tag_store_temp = temporary_dir / "tag-store-abc123.sqlite"

    def build(pbf: PbfSource, _paths: PipelinePaths) -> BuildResult:
        data_temp.write_bytes(b"partial parquet write")
        tag_store_temp.write_bytes(b"active tag store")
        build_started.set()
        assert build_can_finish.wait(timeout=5.0), "Build timed out waiting for release"
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
        del root
        return MetadataResult(data_root / "statistics.json", data_root / "README.md")

    def publish(root: Path) -> PublicationResult:
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

    return _CheckpointRaceFixture(
        paths=paths,
        data_root=data_root,
        data_dir=data_dir,
        manifests_dir=manifests_dir,
        temporary_dir=temporary_dir,
        worker=FakeEnrichmentWorker(),
        build=build,
        metadata=metadata,
        publish=publish,
        data_temp=data_temp,
        tag_store_temp=tag_store_temp,
        build_started=build_started,
        build_can_finish=build_can_finish,
        publish_called=publish_called,
        publish_can_proceed=publish_can_proceed,
        callback_completed=callback_completed,
        race_observed=race_observed,
        callback_started=callback_started,
    )


def _start_pipeline_thread(
    fixture: _CheckpointRaceFixture,
    pipeline_exception: list[BaseException],
) -> threading.Thread:
    def run_pipeline() -> None:
        try:
            run_all(
                fixture.paths,
                build=fixture.build,
                metadata_builder=fixture.metadata,
                publisher=fixture.publish,
                enrichment_worker=fixture.worker,
            )
        except BaseException as exc:
            pipeline_exception.append(exc)

    pipeline_thread = threading.Thread(target=run_pipeline, daemon=True)
    pipeline_thread.start()
    return pipeline_thread


def _start_callback_thread(
    fixture: _CheckpointRaceFixture,
    callback_exception: list[BaseException],
) -> threading.Thread:
    callback = fixture.worker.periodic_callback
    assert callback is not None, "Periodic checkpoint callback was not registered"

    def invoke_callback() -> None:
        fixture.callback_started.wait(timeout=5.0)
        try:
            callback()
        except BaseException as exc:
            callback_exception.append(exc)
        finally:
            fixture.callback_completed.set()

    callback_thread = threading.Thread(target=invoke_callback, daemon=True)
    callback_thread.start()
    fixture.callback_started.wait(timeout=2.0)
    return callback_thread


def _assert_unserialized_publication_race(
    fixture: _CheckpointRaceFixture,
    callback_thread: threading.Thread,
) -> None:
    assert fixture.data_temp.exists(), "Temp files were removed before publish could observe them"
    assert fixture.tag_store_temp.exists()
    fixture.publish_can_proceed.set()
    assert fixture.callback_completed.wait(timeout=5.0), "Callback did not complete"
    callback_thread.join(timeout=2.0)
    assert fixture.race_observed.is_set(), (
        "Race not observed: publication_inventory should have rejected temp files but didn't."
    )
    fixture.build_can_finish.set()


def _assert_serialized_publication(
    fixture: _CheckpointRaceFixture,
    callback_thread: threading.Thread,
) -> None:
    fixture.build_can_finish.set()
    assert fixture.publish_called.wait(timeout=5.0), (
        "publish was not called after build released lock"
    )
    assert not fixture.data_temp.exists(), "Temp files still present after build release"
    assert not fixture.tag_store_temp.exists()
    fixture.publish_can_proceed.set()
    assert fixture.callback_completed.wait(timeout=5.0), (
        "Callback did not complete after lock release"
    )
    callback_thread.join(timeout=2.0)
    assert not fixture.race_observed.is_set(), (
        "Race was observed: publication_inventory saw temp files."
    )


def _run_checkpoint_race(fixture: _CheckpointRaceFixture) -> None:
    pipeline_exception: list[BaseException] = []
    callback_exception: list[BaseException] = []
    pipeline_thread = _start_pipeline_thread(fixture, pipeline_exception)
    try:
        assert fixture.build_started.wait(timeout=2.0), "Build did not start in time"
        assert fixture.data_temp.exists()
        assert fixture.tag_store_temp.exists()
        callback_thread = _start_callback_thread(fixture, callback_exception)
        if fixture.publish_called.wait(timeout=1.0):
            _assert_unserialized_publication_race(fixture, callback_thread)
        else:
            _assert_serialized_publication(fixture, callback_thread)
        assert not callback_thread.is_alive(), "Callback thread did not terminate"
        pipeline_thread.join(timeout=5.0)
        assert not pipeline_thread.is_alive(), "Pipeline thread did not terminate"
        assert not callback_exception, (
            f"Callback raised an unexpected exception: {callback_exception[0]!r}"
        )
        assert not pipeline_exception, (
            f"Pipeline raised an unexpected exception: {pipeline_exception[0]!r}"
        )
    finally:
        fixture.build_can_finish.set()
        fixture.publish_can_proceed.set()
        if pipeline_thread.is_alive():
            pipeline_thread.join(timeout=5.0)


def test_checkpoint_publication_race_with_active_pbf_build(tmp_path: Path) -> None:
    """The refresh lock prevents publication from observing PBF build temps."""
    _run_checkpoint_race(_checkpoint_race_fixture(tmp_path))
