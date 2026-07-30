import signal
from collections.abc import Callable
from pathlib import Path
from typing import cast

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
