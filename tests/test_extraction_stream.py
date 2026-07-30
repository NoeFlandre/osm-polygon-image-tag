import sys
from pathlib import Path

import pytest

from osm_polygon_image_tag.ingest.extraction import (
    STDERR_CAP_BYTES,
    OsmiumExportError,
    osmium_version,
    stream_export,
)

RECORD_1 = b"0103\tway\t1\t1\t1\t2026-01-01T00:00:00Z\t{}\n"
RECORD_2 = b"0103\trelation\t2\t1\t1\t2026-01-01T00:00:00Z\t{}\n"


def _fake_osmium(
    tmp_path: Path,
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    exit_code: int = 0,
    loop: bool = False,
) -> Path:
    program = tmp_path / "fake-osmium.py"
    lines = [f"#!{sys.executable}", "import sys"]
    if loop:
        lines.extend(
            [
                "import time",
                "while True:",
                f"    sys.stdout.buffer.write({stdout!r})",
                "    sys.stdout.buffer.flush()",
                "    time.sleep(0.01)",
            ]
        )
    else:
        lines.append(f"sys.stdout.buffer.write({stdout!r})")
    lines.append(f"sys.stderr.buffer.write({stderr!r})")
    lines.append(f"sys.exit({exit_code})")
    program.write_text("\n".join(lines) + "\n", encoding="utf-8")
    program.chmod(0o755)
    return program


def test_stream_export_yields_ordered_records(tmp_path: Path) -> None:
    executable = _fake_osmium(tmp_path, stdout=RECORD_1 + RECORD_2)

    records = list(
        stream_export(Path("input.osm.pbf"), Path("policy.json"), executable=str(executable))
    )

    assert [(record.osm_type, record.osm_id) for record in records] == [
        ("way", 1),
        ("relation", 2),
    ]


def test_stream_export_wraps_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(OsmiumExportError, match="not found"):
        list(
            stream_export(
                Path("input.osm.pbf"),
                Path("policy.json"),
                executable=str(tmp_path / "missing"),
            )
        )


def test_stream_export_reports_nonzero_exit_and_stderr(tmp_path: Path) -> None:
    executable = _fake_osmium(
        tmp_path,
        stdout=RECORD_1,
        stderr=b"assembly failed\n",
        exit_code=2,
    )

    with pytest.raises(OsmiumExportError, match="exited 2") as captured:
        list(stream_export(Path("input.osm.pbf"), Path("policy.json"), executable=str(executable)))

    assert captured.value.stderr == b"assembly failed\n"


def test_stream_export_bounds_retained_stderr(tmp_path: Path) -> None:
    executable = _fake_osmium(
        tmp_path,
        stderr=b"X" * (STDERR_CAP_BYTES + 1024 * 1024),
        exit_code=1,
    )

    with pytest.raises(OsmiumExportError) as captured:
        list(stream_export(Path("input.osm.pbf"), Path("policy.json"), executable=str(executable)))

    assert len(captured.value.stderr) == STDERR_CAP_BYTES


def test_stream_export_terminates_child_when_consumer_stops(tmp_path: Path) -> None:
    executable = _fake_osmium(tmp_path, stdout=RECORD_1, loop=True)
    records = stream_export(
        Path("input.osm.pbf"),
        Path("policy.json"),
        executable=str(executable),
    )

    assert next(records).osm_id == 1
    records.close()


def test_osmium_version_returns_first_line(tmp_path: Path) -> None:
    executable = _fake_osmium(tmp_path, stdout=b"osmium version 1.19.1\nlibosmium\n")

    assert osmium_version(executable=str(executable)) == "osmium version 1.19.1"


def test_osmium_version_rejects_missing_or_empty_executable(tmp_path: Path) -> None:
    with pytest.raises(OsmiumExportError, match="not found"):
        osmium_version(executable=str(tmp_path / "missing"))

    executable = _fake_osmium(tmp_path)
    with pytest.raises(OsmiumExportError, match="no version"):
        osmium_version(executable=str(executable))
