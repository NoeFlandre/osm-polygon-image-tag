import os
from pathlib import Path, PurePosixPath

import pytest

from osm_polygon_image_tag.discovery import PbfSource, discover_pbfs
from osm_polygon_image_tag.errors import ConfigurationError


def test_discovers_only_pbf_files_in_relative_path_order(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    (source / "z").mkdir(parents=True)
    (source / "a").mkdir()
    (source / "z" / "two.osm.pbf").write_bytes(b"22")
    (source / "a" / "one.osm.pbf").write_bytes(b"1")
    (source / "ignore.txt").write_text("x", encoding="utf-8")

    assert discover_pbfs(source) == (
        PbfSource(
            relative_path=PurePosixPath("a/one.osm.pbf"),
            absolute_path=(source / "a" / "one.osm.pbf").resolve(),
            size_bytes=1,
        ),
        PbfSource(
            relative_path=PurePosixPath("z/two.osm.pbf"),
            absolute_path=(source / "z" / "two.osm.pbf").resolve(),
            size_bytes=2,
        ),
    )


def test_empty_inventory_is_valid_and_read_only(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    before = tuple(source.iterdir())

    assert discover_pbfs(source) == ()
    assert tuple(source.iterdir()) == before


def test_rejects_symlink_anywhere_in_source_tree(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    target = tmp_path / "outside.osm.pbf"
    target.write_bytes(b"x")
    (source / "linked.osm.pbf").symlink_to(target)

    with pytest.raises(ConfigurationError, match="symlink"):
        discover_pbfs(source)


def test_rejects_non_regular_matching_entry(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    os.mkfifo(source / "named-pipe.osm.pbf")

    with pytest.raises(ConfigurationError, match="regular file"):
        discover_pbfs(source)
