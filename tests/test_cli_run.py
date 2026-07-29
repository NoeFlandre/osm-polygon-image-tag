import json
from pathlib import Path

from _pytest.capture import CaptureFixture

from osm_polygon_image_tag.cli import run
from osm_polygon_image_tag.orchestrator import RunSummary, VerifySummary


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
