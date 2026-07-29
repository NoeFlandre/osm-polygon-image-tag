import json
from pathlib import Path

import pytest

from osm_polygon_image_tag.extraction import (
    ExportRecord,
    export_command,
    has_target_tag,
    iter_records,
    parse_copy_record,
)


def test_export_command_is_fixed_and_polygon_only() -> None:
    assert export_command(Path("/input/a.osm.pbf"), Path("/config.json")) == (
        "osmium",
        "export",
        "/input/a.osm.pbf",
        "--output-format",
        "pg",
        "--config",
        "/config.json",
        "--geometry-types",
        "polygon",
        "--output",
        "-",
    )


def test_copy_parser_preserves_geometry_provenance_and_tags() -> None:
    record = parse_copy_record(
        b"0103000020E6100000\tway\t42\t3\t99\t"
        b"2026-01-01T00:00:00Z\t"
        b'{"image":"File:View.jpg","name":"Place","__osm_id":"source tag"}\n'
    )

    assert record == ExportRecord(
        geometry_ewkb_hex="0103000020E6100000",
        osm_type="way",
        osm_id=42,
        version=3,
        changeset=99,
        timestamp="2026-01-01T00:00:00Z",
        tags={"image": "File:View.jpg", "name": "Place", "__osm_id": "source tag"},
    )


def test_copy_parser_handles_nulls_and_postgres_escaped_json() -> None:
    value = "a\tb\\c\nline"
    tags_json = json.dumps({"mapillary": value}, separators=(",", ":"))
    copy_field = tags_json.replace("\\", "\\\\").encode()

    record = parse_copy_record(b"0103\trelation\t7\t\\N\t\\N\t\\N\t" + copy_field + b"\n")

    assert record.version is None
    assert record.changeset is None
    assert record.timestamp is None
    assert record.tags == {"mapillary": value}


def test_copy_parser_treats_empty_optional_metadata_as_null() -> None:
    record = parse_copy_record(b"0103\tway\t7\t\t\t\t{}\n")

    assert record.version is None
    assert record.changeset is None
    assert record.timestamp is None


@pytest.mark.parametrize(
    "line",
    [
        b"0103\tway\t42\n",
        b"0103\tway\t42\t1\t1\t2026-01-01T00:00:00Z\t{not json}\n",
        b"0103\tway\tnot-an-id\t1\t1\t2026-01-01T00:00:00Z\t{}\n",
        b"0103\tway\t42\t1\t1\t2026-01-01T00:00:00Z\t[]\n",
        b'0103\tway\t42\t1\t1\t2026-01-01T00:00:00Z\t{"image":3}\n',
    ],
)
def test_copy_parser_rejects_malformed_records(line: bytes) -> None:
    with pytest.raises(ValueError):
        parse_copy_record(line)


def test_record_iterator_skips_blanks_and_reports_line_number() -> None:
    good = b"0103\tway\t42\t1\t1\t2026-01-01T00:00:00Z\t{}\n"
    assert [record.osm_id for record in iter_records([b"\n", good, b" \t\n"])] == [42]

    with pytest.raises(ValueError, match="line 2"):
        list(iter_records([good, b"bad\n"]))


def test_export_record_is_frozen() -> None:
    record = parse_copy_record(b"0103\tway\t1\t1\t1\t2026-01-01T00:00:00Z\t{}\n")

    with pytest.raises(AttributeError):
        record.osm_id = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "key",
    ["image", "wikimedia_commons", "mapillary", "panoramax", "kartaview", "flickr"],
)
def test_each_target_key_matches_by_presence_including_empty_value(key: str) -> None:
    assert has_target_tag({key: ""})


@pytest.mark.parametrize(
    "tags",
    [
        {},
        {"name": "Place"},
        {"image:license": "CC0"},
        {"contact:flickr": "account"},
        {"wikimedia": "commons"},
    ],
)
def test_similarly_named_or_unrelated_keys_do_not_match(tags: dict[str, str]) -> None:
    assert not has_target_tag(tags)
