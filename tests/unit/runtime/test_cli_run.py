import json
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture
from pytest import MonkeyPatch

from osm_polygon_image_tag.artifacts.publication import EXPECTED_REPO, PublicationResult
from osm_polygon_image_tag.artifacts.reporting import MetadataResult
from osm_polygon_image_tag.cli import _emit_progress, _run_with_signals, run
from osm_polygon_image_tag.core.config import PipelinePaths
from osm_polygon_image_tag.runtime.orchestrator import RunSummary, VerifySummary


def test_run_command_emits_canonical_local_summary(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    expected = RunSummary(processed=2, built=1, skipped=1, accepted_rows=3, stopped=False)

    exit_code = run(
        ["run", "--source-root", str(source), "--data-root", str(tmp_path / "output")],
        execute_run=lambda _paths: expected,
    )

    assert exit_code == 0
    assert capsys.readouterr().out == json.dumps(expected.to_dict(), sort_keys=True) + "\n"


def test_verify_command_emits_canonical_summary(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    expected = VerifySummary(checked=2, valid=1, invalid=1)

    exit_code = run(
        ["verify", "--source-root", str(source), "--data-root", str(tmp_path / "output")],
        execute_verify=lambda _paths: expected,
    )

    assert exit_code == 0
    assert capsys.readouterr().out == json.dumps(expected.to_dict(), sort_keys=True) + "\n"


def test_publish_command_requires_and_forwards_exact_confirmation(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    expected = PublicationResult("published", "abc", 4)

    def publish(_paths: object, confirmation: str) -> PublicationResult:
        assert confirmation == EXPECTED_REPO
        return expected

    exit_code = run(
        [
            "publish",
            "--source-root",
            str(source),
            "--data-root",
            str(tmp_path / "output"),
            "--confirm-repo",
            EXPECTED_REPO,
        ],
        execute_publish=publish,
    )

    assert exit_code == 0
    assert capsys.readouterr().out == json.dumps(expected.to_dict(), sort_keys=True) + "\n"


@pytest.mark.parametrize("command", ["publish", "run-and-publish"])
def test_publication_commands_reject_wrong_confirmation_before_execution(
    tmp_path: Path, capsys: CaptureFixture[str], command: str
) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    called = False

    def execute_publish(_paths: object, _confirmation: str) -> PublicationResult:
        nonlocal called
        called = True
        raise AssertionError

    def execute_run_publish(_paths: object, _confirmation: str) -> RunSummary:
        nonlocal called
        called = True
        raise AssertionError

    exit_code = run(
        [
            command,
            "--source-root",
            str(source),
            "--data-root",
            str(tmp_path / "output"),
            "--confirm-repo",
            "wrong/repo",
        ],
        execute_publish=execute_publish,
        execute_run_publish=execute_run_publish,
    )

    assert exit_code == 2
    assert called is False
    assert "confirmation" in capsys.readouterr().err


def test_progress_events_are_canonical_json_on_stderr(
    capsys: CaptureFixture[str],
) -> None:
    _emit_progress({"source_pbf": "a.osm.pbf", "event": "pbf_started", "pbf_index": 1})

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        'progress {"event":"pbf_started","pbf_index":1,"source_pbf":"a.osm.pbf"}\n'
    )


def test_json_log_format_remains_machine_readable(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    expected = RunSummary(processed=0, built=0, skipped=0, accepted_rows=0, stopped=False)

    exit_code = run(
        [
            "run",
            "--source-root",
            str(source),
            "--data-root",
            str(tmp_path / "output"),
            "--log-format",
            "json",
        ],
        execute_run=lambda _paths: expected,
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out == json.dumps(expected.to_dict(), sort_keys=True) + "\n"
    assert "\x1b[" not in captured.out + captured.err


def test_rebuild_metadata_forwards_external_asset_checkpoint(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    expected = MetadataResult(tmp_path / "statistics.json", tmp_path / "README.md")
    captured: dict[str, object] = {}

    def execute_metadata(data_root: Path, checkpoint_root: Path | None) -> MetadataResult:
        captured["data_root"] = data_root
        captured["checkpoint_root"] = checkpoint_root
        return expected

    checkpoint = tmp_path / "scratch"
    exit_code = run(
        [
            "rebuild-metadata",
            "--source-root",
            str(source),
            "--data-root",
            str(tmp_path / "output"),
            "--asset-checkpoint-root",
            str(checkpoint),
        ],
        execute_metadata_with_checkpoint=execute_metadata,
    )

    assert exit_code == 0
    assert captured == {
        "data_root": (tmp_path / "output").resolve(),
        "checkpoint_root": checkpoint,
    }
    assert capsys.readouterr().out == json.dumps(expected.to_dict(), sort_keys=True) + "\n"


def test_production_run_wires_the_enrichment_worker(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    paths = PipelinePaths.build(source_root=source, data_root=tmp_path / "output")
    worker = object()

    monkeypatch.setattr(
        "osm_polygon_image_tag.cli._build_enrichment_worker",
        lambda _paths, _token, _progress: worker,
    )

    def execute(paths: PipelinePaths, **kwargs: object) -> RunSummary:
        assert kwargs["enrichment_worker"] is worker
        return RunSummary(0, 0, 0, 0, False)

    monkeypatch.setattr("osm_polygon_image_tag.cli.run_all", execute)

    assert _run_with_signals(paths).enrichment.built == 0
