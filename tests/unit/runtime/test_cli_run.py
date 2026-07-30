import json
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture

from osm_polygon_image_tag.artifacts.publication import EXPECTED_REPO, PublicationResult
from osm_polygon_image_tag.cli import _emit_progress, run
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
