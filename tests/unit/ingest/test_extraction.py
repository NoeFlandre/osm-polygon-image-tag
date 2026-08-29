import json
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

import osm_polygon_image_tag.ingest.copy_parser as copy_parser
from osm_polygon_image_tag.ingest.extraction import (
    ExportRecord,
    export_command,
    has_target_tag,
    is_target_tag_key,
    iter_records,
    parse_copy_record,
    restore_original_tags,
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


def test_copy_parser_uses_direct_decode_for_unescaped_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_escape_decode(_field: bytes) -> str:
        raise AssertionError("unescaped fields should not use the escape decoder")

    monkeypatch.setattr(
        copy_parser,
        "_decode_escaped_copy_field",
        unexpected_escape_decode,
    )

    assert copy_parser._decode_copy_field("Café".encode()) == "Café"


def test_restore_original_tags_uses_bounded_batch_lookup_in_order() -> None:
    records = [
        parse_copy_record(f"0103\tway\t{osm_id}\t1\t1\t\\N\t{{}}\n".encode())
        for osm_id in (1, 2, 3)
    ]
    calls: list[tuple[tuple[str, int], ...]] = []

    def unexpected_lookup(_osm_type: str, _osm_id: int) -> None:
        raise AssertionError("single-record lookup should not be used")

    def lookup_many(
        identities: Iterable[tuple[str, int]],
    ) -> Mapping[tuple[str, int], Mapping[str, str]]:
        batch = tuple(identities)
        calls.append(batch)
        return {
            identity: {"image": f"image-{identity[1]}"}
            for identity in batch
            if identity != ("way", 2)
        }

    restored = list(
        restore_original_tags(
            records,
            lookup=unexpected_lookup,
            lookup_many=lookup_many,
            batch_size=2,
        )
    )

    assert calls == [(("way", 1), ("way", 2)), (("way", 3),)]
    assert [(record.osm_id, record.tags) for record in restored] == [
        (1, {"image": "image-1"}),
        (3, {"image": "image-3"}),
    ]


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
        setattr(record, "osm_id", 2)  # noqa: B010


@pytest.mark.parametrize(
    "key",
    [
        "image",
        "wikimedia_commons",
        "mapillary",
        "panoramax",
        "panoramax:0",
        "panoramax:27",
        "kartaview",
        "flickr",
        "bubbleid",
    ],
)
def test_each_target_key_matches_with_non_empty_value(key: str) -> None:
    assert is_target_tag_key(key)
    assert has_target_tag({key: "reference"})
    assert not has_target_tag({key: ""})


@pytest.mark.parametrize(
    "tags",
    [
        {},
        {"name": "Place"},
        {"image:license": "CC0"},
        {"contact:flickr": "account"},
        {"wikimedia": "commons"},
        {"panoramax:left": "reference"},
        {"panoramax:": "reference"},
        {"panoramax:1:foo": "reference"},
    ],
)
def test_similarly_named_or_unrelated_keys_do_not_match(tags: dict[str, str]) -> None:
    assert not has_target_tag(tags)
