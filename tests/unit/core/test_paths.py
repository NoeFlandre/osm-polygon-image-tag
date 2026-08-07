from pathlib import Path

import pytest

from osm_polygon_image_tag.core.paths import resolve_managed_output


def test_resolve_managed_output_returns_existing_path_inside_root(tmp_path: Path) -> None:
    output = tmp_path / "data" / "region.parquet"
    output.parent.mkdir()
    output.write_bytes(b"output")

    assert resolve_managed_output(tmp_path, "data/region.parquet") == output


@pytest.mark.parametrize("relative_path", ["../outside.parquet", "/outside.parquet"])
def test_resolve_managed_output_rejects_paths_outside_root(
    tmp_path: Path, relative_path: str
) -> None:
    with pytest.raises(ValueError, match="escapes data root"):
        resolve_managed_output(tmp_path, relative_path)


def test_resolve_managed_output_rejects_symlinked_file(tmp_path: Path) -> None:
    target = tmp_path / "outside.parquet"
    target.write_bytes(b"target")
    linked = tmp_path / "data" / "region.parquet"
    linked.parent.mkdir()
    linked.symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        resolve_managed_output(tmp_path, "data/region.parquet")


def test_resolve_managed_output_rejects_symlinked_parent(tmp_path: Path) -> None:
    target = tmp_path / "external"
    target.mkdir()
    (target / "region.parquet").write_bytes(b"target")
    (tmp_path / "data").symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        resolve_managed_output(tmp_path, "data/region.parquet")
