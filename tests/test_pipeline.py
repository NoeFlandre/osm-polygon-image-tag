from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest
from shapely import to_wkb
from shapely.geometry import Polygon

import osm_polygon_image_tag.pipeline as pipeline
from osm_polygon_image_tag.core.config import PipelinePaths
from osm_polygon_image_tag.discovery import PbfSource
from osm_polygon_image_tag.extraction import ExportRecord, SourceTagRecord
from osm_polygon_image_tag.pipeline import build_one, verify_one


def _source(path: Path) -> PbfSource:
    return PbfSource(PurePosixPath("nested/region.osm.pbf"), path, path.stat().st_size)


def _record(osm_id: int, *, tags: dict[str, str] | None = None) -> ExportRecord:
    return ExportRecord(
        geometry_ewkb_hex=to_wkb(
            Polygon([(0, 0), (0, 0.1), (0.1, 0.1), (0.1, 0)]),
            hex=True,
        ),
        osm_type="way",
        osm_id=osm_id,
        version=1,
        changeset=2,
        timestamp=None,
        tags=tags or {"image": "export-value"},
    )


def _scanner(records: list[SourceTagRecord], calls: list[str]) -> Callable[..., None]:
    def scan(_path: Path, *, emit: Callable[[SourceTagRecord], None]) -> None:
        calls.append("scan")
        for record in records:
            emit(record)

    return scan


def _exporter(
    records: list[ExportRecord], calls: list[str]
) -> Callable[..., Iterable[ExportRecord]]:
    def export(_pbf: Path, _config: Path, *, executable: str) -> Iterable[ExportRecord]:
        calls.append(f"export:{executable}")
        return iter(records)

    return export


def _version(**_kwargs: object) -> str:
    return "osmium version test"


def test_builds_then_skips_only_verified_identical_shard(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    pbf = raw / "region.osm.pbf"
    pbf.write_bytes(b"source")
    paths = PipelinePaths.build(source_root=raw, data_root=tmp_path / "generated")
    calls: list[str] = []
    scanner = _scanner([SourceTagRecord("way", 1, {"image": "exact", "name": "A"})], calls)
    exporter = _exporter([_record(1)], calls)

    built = build_one(
        _source(pbf),
        paths,
        scanner=scanner,
        exporter=exporter,
        version_getter=_version,
    )
    skipped = build_one(
        _source(pbf),
        paths,
        scanner=scanner,
        exporter=exporter,
        version_getter=_version,
    )

    assert built.status == "built"
    assert built.accepted_rows == 1
    assert skipped.status == "skipped"
    assert calls == ["scan", "export:osmium"]
    assert built.output_path.exists()
    assert built.manifest_path.exists()


def test_resume_fast_path_does_not_rehash_finalized_source_or_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    pbf = raw / "region.osm.pbf"
    pbf.write_bytes(b"source")
    paths = PipelinePaths.build(source_root=raw, data_root=tmp_path / "generated")
    scanner = _scanner([SourceTagRecord("way", 1, {"image": "exact"})], [])
    exporter = _exporter([_record(1)], [])
    build_one(
        _source(pbf),
        paths,
        scanner=scanner,
        exporter=exporter,
        version_getter=_version,
    )

    def unexpected_hash(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("resume rehashed a finalized artifact")

    monkeypatch.setattr(pipeline, "source_identity", unexpected_hash)
    monkeypatch.setattr(pipeline, "file_sha256", unexpected_hash)
    monkeypatch.setattr(pipeline, "validate_geoparquet", unexpected_hash)

    resumed = build_one(
        _source(pbf),
        paths,
        scanner=scanner,
        exporter=exporter,
        version_getter=_version,
    )

    assert resumed.status == "skipped"


def test_explicit_verify_retains_deep_digest_validation(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    pbf = raw / "region.osm.pbf"
    pbf.write_bytes(b"source")
    paths = PipelinePaths.build(source_root=raw, data_root=tmp_path / "generated")
    source = _source(pbf)
    result = build_one(
        source,
        paths,
        scanner=_scanner([SourceTagRecord("way", 1, {"image": "exact"})], []),
        exporter=_exporter([_record(1)], []),
        version_getter=_version,
    )

    assert verify_one(source, paths) is True
    content = bytearray(result.output_path.read_bytes())
    content[len(content) // 2] ^= 1
    result.output_path.write_bytes(content)

    assert verify_one(source, paths) is False


def test_source_drift_and_output_corruption_each_force_rebuild(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    pbf = raw / "region.osm.pbf"
    pbf.write_bytes(b"v1")
    paths = PipelinePaths.build(source_root=raw, data_root=tmp_path / "generated")
    calls: list[str] = []
    scanner = _scanner([SourceTagRecord("way", 1, {"image": "exact"})], calls)
    exporter = _exporter([_record(1)], calls)
    first = build_one(
        _source(pbf), paths, scanner=scanner, exporter=exporter, version_getter=_version
    )

    pbf.write_bytes(b"v2")
    second = build_one(
        _source(pbf), paths, scanner=scanner, exporter=exporter, version_getter=_version
    )
    second.output_path.write_bytes(b"corrupt")
    third = build_one(
        _source(pbf), paths, scanner=scanner, exporter=exporter, version_getter=_version
    )

    assert [first.status, second.status, third.status] == ["built", "built", "built"]
    assert calls == [
        "scan",
        "export:osmium",
        "scan",
        "export:osmium",
        "scan",
        "export:osmium",
    ]


def test_counts_rejections_without_emitting_degraded_rows(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    pbf = raw / "region.osm.pbf"
    pbf.write_bytes(b"source")
    paths = PipelinePaths.build(source_root=raw, data_root=tmp_path / "generated")

    result = build_one(
        _source(pbf),
        paths,
        scanner=_scanner(
            [
                SourceTagRecord("way", 1, {"image": "good"}),
                SourceTagRecord("way", 2, {"image": "bad"}),
            ],
            [],
        ),
        exporter=_exporter([_record(1), replace(_record(2), geometry_ewkb_hex="zz")], []),
        version_getter=_version,
    )

    assert result.accepted_rows == 1
    assert result.rejections == {"malformed_wkb": 1}
