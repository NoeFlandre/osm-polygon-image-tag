import json
import subprocess
import threading
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO

import osmium

from osm_polygon_image_tag.errors import ImageTagPipelineError

TARGET_TAG_KEYS = (
    "image",
    "wikimedia_commons",
    "mapillary",
    "panoramax",
    "kartaview",
    "flickr",
    "bubbleid",
)
STDERR_CAP_BYTES = 64 * 1024

_COPY_ESCAPES = {
    ord("b"): b"\b",
    ord("f"): b"\f",
    ord("n"): b"\n",
    ord("r"): b"\r",
    ord("t"): b"\t",
    ord("v"): b"\v",
    ord("\\"): b"\\",
}


@dataclass(frozen=True, slots=True)
class ExportRecord:
    geometry_ewkb_hex: str
    osm_type: str
    osm_id: int
    version: int | None
    changeset: int | None
    timestamp: str | None
    tags: dict[str, str]


@dataclass(frozen=True, slots=True)
class SourceTagRecord:
    osm_type: str
    osm_id: int
    tags: dict[str, str]


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


def _decode_copy_field(field: bytes) -> str:
    decoded = bytearray()
    index = 0
    while index < len(field):
        value = field[index]
        if value != ord("\\"):
            decoded.append(value)
            index += 1
            continue
        index += 1
        if index >= len(field):
            raise ValueError("COPY field ends with an incomplete escape")
        escaped = field[index]
        decoded.extend(_COPY_ESCAPES.get(escaped, bytes((escaped,))))
        index += 1
    return decoded.decode("utf-8")


def _optional_text(field: bytes) -> str | None:
    return None if field in {b"", b"\\N"} else _decode_copy_field(field)


def _optional_int(field: bytes, *, name: str) -> int | None:
    value = _optional_text(field)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer or null") from error


def _parse_tags(field: bytes) -> dict[str, str]:
    try:
        value: Any = json.loads(_decode_copy_field(field))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("tags must be valid UTF-8 JSON") from error
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError("tags must be a JSON object of string keys and values")
    return dict(value)


def parse_copy_record(line: bytes) -> ExportRecord:
    fields = line.rstrip(b"\r\n").split(b"\t")
    if len(fields) != 7:
        raise ValueError(f"expected 7 COPY fields, received {len(fields)}")
    geometry, osm_type, osm_id, version, changeset, timestamp, tags = fields
    try:
        required_id = int(_decode_copy_field(osm_id))
    except ValueError as error:
        raise ValueError("osm_id must be an integer") from error
    return ExportRecord(
        geometry_ewkb_hex=_decode_copy_field(geometry),
        osm_type=_decode_copy_field(osm_type),
        osm_id=required_id,
        version=_optional_int(version, name="version"),
        changeset=_optional_int(changeset, name="changeset"),
        timestamp=_optional_text(timestamp),
        tags=_parse_tags(tags),
    )


def iter_records(lines: Iterable[bytes]) -> Iterator[ExportRecord]:
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            yield parse_copy_record(line)
        except ValueError as error:
            message = f"malformed osmium COPY record at line {line_number}: {error}"
            raise ValueError(message) from error


def is_target_tag_key(key: str) -> bool:
    if key in TARGET_TAG_KEYS:
        return True
    prefix = "panoramax:"
    suffix = key.removeprefix(prefix)
    return key.startswith(prefix) and suffix.isascii() and suffix.isdigit()


def has_target_tag(tags: Mapping[str, str]) -> bool:
    return any(value != "" and is_target_tag_key(key) for key, value in tags.items())


def panoramax_tag_values(tags: Mapping[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in sorted(tags.items())
        if value != ""
        and (key == "panoramax" or (is_target_tag_key(key) and key.startswith("panoramax:")))
    }


class _SourceTagHandler(osmium.SimpleHandler):
    def __init__(self, emit: Callable[[SourceTagRecord], None]) -> None:
        super().__init__()
        self._emit = emit

    def _handle(self, osm_type: str, osm_object: Any) -> None:
        tags = dict(osm_object.tags)
        if has_target_tag(tags):
            self._emit(
                SourceTagRecord(
                    osm_type=osm_type,
                    osm_id=int(osm_object.id),
                    tags=tags,
                )
            )

    def way(self, way: Any) -> None:
        self._handle("way", way)

    def relation(self, relation: Any) -> None:
        self._handle("relation", relation)


def scan_target_source_tags(
    pbf_path: Path,
    *,
    emit: Callable[[SourceTagRecord], None],
) -> None:
    _SourceTagHandler(emit).apply_file(str(pbf_path), locations=False)


def restore_original_tags(
    records: Iterable[ExportRecord],
    *,
    lookup: Callable[[str, int], Mapping[str, str] | None],
) -> Iterator[ExportRecord]:
    for record in records:
        tags = lookup(record.osm_type, record.osm_id)
        if tags is not None:
            yield replace(record, tags=dict(tags))


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
