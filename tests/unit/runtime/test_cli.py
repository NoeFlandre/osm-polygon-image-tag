import json
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture

from osm_polygon_image_tag.cli import run
from osm_polygon_image_tag.runtime.preflight import Capacity, PreflightReport, ToolVersion

EXPECTED_COMMANDS = {
    "preflight",
    "publish",
    "rebuild-metadata",
    "run",
    "run-and-publish",
    "verify",
}


def test_help_lists_exactly_the_public_commands(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run(["--help"])

    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert {command for command in EXPECTED_COMMANDS if command in help_text} == EXPECTED_COMMANDS


@pytest.mark.parametrize(
    ("command", "requires_confirmation"),
    [
        ("preflight", False),
        ("run", False),
        ("verify", False),
        ("rebuild-metadata", False),
        ("publish", True),
        ("run-and-publish", True),
    ],
)
def test_command_help_preserves_option_names(
    command: str,
    requires_confirmation: bool,
    capsys: CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run([command, "--help"])

    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "--source-root" in help_text
    assert "--data-root" in help_text
    assert "OSM_POLYGON_IMAGE_TAG_DATA_ROOT" in help_text
    assert ("--confirm-repo" in help_text) is requires_confirmation
    assert "--log-format" in help_text


def test_rebuild_metadata_help_exposes_external_asset_checkpoint(
    capsys: CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run(["rebuild-metadata", "--help"])

    assert exit_info.value.code == 0
    assert "--asset-checkpoint-root" in capsys.readouterr().out


def test_preflight_command_emits_canonical_json(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    output = tmp_path / "output"
    expected = PreflightReport(
        source_root=str(source.resolve()),
        data_root=str(output.resolve()),
        pbf_count=0,
        pbf_bytes=0,
        osmium=ToolVersion(path="/usr/bin/osmium", version="osmium 1.19.1"),
        capacity=Capacity(free_bytes=5, total_bytes=10),
    )

    exit_code = run(
        ["preflight", "--source-root", str(source), "--data-root", str(output)],
        execute_preflight=lambda _paths: expected,
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == json.dumps(expected.to_dict(), sort_keys=True) + "\n"


def test_expected_operator_error_returns_exit_two(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing"

    exit_code = run(
        ["preflight", "--source-root", str(missing), "--data-root", str(tmp_path / "out")]
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err
