from pathlib import Path

from osm_polygon_image_tag.config import PipelinePaths
from osm_polygon_image_tag.discovery import PbfSource
from osm_polygon_image_tag.orchestrator import StopToken, run_all
from osm_polygon_image_tag.pipeline import BuildResult
from osm_polygon_image_tag.publication import PublicationResult
from osm_polygon_image_tag.reporting import MetadataResult


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
