"""Run osmium safely and stream parsed area records."""

import subprocess
import threading
from collections.abc import Generator
from pathlib import Path
from typing import BinaryIO

from osm_polygon_image_tag.core.errors import ImageTagPipelineError
from osm_polygon_image_tag.ingest.copy_parser import ExportRecord, iter_records

STDERR_CAP_BYTES = 64 * 1024


class OsmiumExportError(ImageTagPipelineError):
    def __init__(self, message: str, *, stderr: bytes = b"") -> None:
        super().__init__(message)
        self.stderr = stderr


class _BoundedBytes:
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._buffer = bytearray()

    def append(self, chunk: bytes) -> None:
        if len(chunk) >= self._capacity:
            self._buffer[:] = chunk[-self._capacity :]
            return
        overflow = len(self._buffer) + len(chunk) - self._capacity
        if overflow > 0:
            del self._buffer[:overflow]
        self._buffer.extend(chunk)

    def value(self) -> bytes:
        return bytes(self._buffer)


def export_command(
    pbf_path: Path,
    config_path: Path,
    *,
    executable: str = "osmium",
) -> tuple[str, ...]:
    return (
        executable,
        "export",
        str(pbf_path),
        "--output-format",
        "pg",
        "--config",
        str(config_path),
        "--geometry-types",
        "polygon",
        "--output",
        "-",
    )


def _drain_stderr(stream: BinaryIO, retained: _BoundedBytes) -> None:
    while chunk := stream.read(8192):
        retained.append(chunk)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def stream_export(
    pbf_path: Path,
    config_path: Path,
    *,
    executable: str = "osmium",
) -> Generator[ExportRecord, None, None]:
    command = export_command(pbf_path, config_path, executable=executable)
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv; no shell.
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except FileNotFoundError as error:
        raise OsmiumExportError(f"osmium executable not found: {executable}") from error
    if process.stdout is None or process.stderr is None:
        _stop_process(process)
        raise OsmiumExportError("osmium pipes were not created")

    retained = _BoundedBytes(STDERR_CAP_BYTES)
    stderr_thread = threading.Thread(
        target=_drain_stderr,
        args=(process.stderr, retained),
        name="osmium-stderr",
        daemon=True,
    )
    stderr_thread.start()
    try:
        yield from iter_records(process.stdout)
        return_code = process.wait()
        stderr_thread.join(timeout=5)
        if return_code != 0:
            raise OsmiumExportError(
                f"osmium export exited {return_code}",
                stderr=retained.value(),
            )
    finally:
        _stop_process(process)
        process.stdout.close()
        stderr_thread.join(timeout=5)
        process.stderr.close()


def osmium_version(*, executable: str = "osmium") -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed version argv; no shell.
            [executable, "--version"],
            check=False,
            capture_output=True,
            shell=False,
            timeout=10,
        )
    except FileNotFoundError as error:
        raise OsmiumExportError(f"osmium executable not found: {executable}") from error
    except subprocess.TimeoutExpired as error:
        raise OsmiumExportError("osmium version probe timed out") from error
    if completed.returncode != 0:
        raise OsmiumExportError(
            f"osmium version probe exited {completed.returncode}",
            stderr=completed.stderr[-STDERR_CAP_BYTES:],
        )
    lines = completed.stdout.decode("utf-8", errors="replace").splitlines()
    if not lines:
        raise OsmiumExportError("osmium returned no version text")
    return lines[0].strip()
