from pathlib import Path

import pytest

import osm_polygon_image_tag.core.config as config
from osm_polygon_image_tag.core.config import PipelinePaths, resolve_data_root
from osm_polygon_image_tag.core.errors import ConfigurationError


def test_resolve_data_root_prefers_an_explicit_path(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit"

    assert resolve_data_root(explicit) == explicit


def test_resolve_data_root_uses_external_project_root_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preferred = tmp_path / "external" / "osm-polygon-image-tag"
    preferred.parent.mkdir()
    monkeypatch.setattr(config, "DEFAULT_DATA_ROOT", preferred)
    monkeypatch.delenv(config.DATA_ROOT_ENVIRONMENT_VARIABLE, raising=False)

    assert resolve_data_root(None) == preferred


def test_resolve_data_root_honors_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preferred = tmp_path / "external" / "osm-polygon-image-tag"
    configured = tmp_path / "configured"
    preferred.parent.mkdir()
    monkeypatch.setattr(config, "DEFAULT_DATA_ROOT", preferred)
    monkeypatch.setenv(config.DATA_ROOT_ENVIRONMENT_VARIABLE, str(configured))

    assert resolve_data_root(None) == configured


def test_resolve_data_root_requires_an_explicit_path_without_external_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DEFAULT_DATA_ROOT", tmp_path / "unmounted" / "data")
    monkeypatch.delenv(config.DATA_ROOT_ENVIRONMENT_VARIABLE, raising=False)

    with pytest.raises(ConfigurationError, match="--data-root"):
        resolve_data_root(None)


def test_accepts_separate_existing_source_and_output(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    output = tmp_path / "image-data"
    source.mkdir()

    paths = PipelinePaths.build(source_root=source, data_root=output)

    assert paths.source_root == source.resolve()
    assert paths.data_root == output.resolve()
    assert not output.exists()


@pytest.mark.parametrize(
    ("source_suffix", "output_suffix"),
    [
        ("raw", "raw"),
        ("raw", "raw/output"),
        ("raw/nested", "raw"),
    ],
)
def test_rejects_equal_or_nested_roots(
    tmp_path: Path, source_suffix: str, output_suffix: str
) -> None:
    source = tmp_path / source_suffix
    source.mkdir(parents=True)

    with pytest.raises(ConfigurationError, match="must not overlap"):
        PipelinePaths.build(
            source_root=source,
            data_root=tmp_path / output_suffix,
        )


def test_resolves_parent_traversal_before_boundary_check(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()

    with pytest.raises(ConfigurationError, match="must not overlap"):
        PipelinePaths.build(source_root=source, data_root=source / ".." / "raw" / "out")


def test_rejects_a_symlinked_source_root(tmp_path: Path) -> None:
    real_source = tmp_path / "real-raw"
    real_source.mkdir()
    linked_source = tmp_path / "linked-raw"
    linked_source.symlink_to(real_source, target_is_directory=True)

    with pytest.raises(ConfigurationError, match="source root must not be a symlink"):
        PipelinePaths.build(source_root=linked_source, data_root=tmp_path / "output")


def test_rejects_a_source_root_that_is_not_a_directory(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.write_bytes(b"not a directory")

    with pytest.raises(ConfigurationError, match="source root must be a directory"):
        PipelinePaths.build(source_root=source, data_root=tmp_path / "output")


def test_build_never_creates_output_directories(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    output = tmp_path / "output"
    source.mkdir()

    PipelinePaths.build(source_root=source, data_root=output)

    assert list(tmp_path.iterdir()) == [source]
