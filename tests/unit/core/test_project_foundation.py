from importlib.metadata import metadata, version
from pathlib import Path
from tomllib import loads

import osm_polygon_image_tag


def test_distribution_and_package_versions_match() -> None:
    assert version("osm-polygon-image-tag") == "0.1.0"
    assert osm_polygon_image_tag.__version__ == "0.1.0"


def test_public_metadata_targets_only_this_project() -> None:
    project = metadata("osm-polygon-image-tag")
    assert project["Name"] == "osm-polygon-image-tag"
    assert "image-reference tags" in project["Summary"]

    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "osm-polygon-description-tag" not in pyproject
    assert "osm-polygon-wikidata-only" not in pyproject


def test_default_pytest_command_enforces_coverage() -> None:
    pyproject = loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    addopts = pyproject["tool"]["pytest"]["ini_options"]["addopts"]
    assert "--cov=osm_polygon_image_tag" in addopts
    assert "--cov-report=term-missing" in addopts


def test_required_project_toolchain_is_declared() -> None:
    pyproject = loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    dependencies = "\n".join(pyproject["project"]["dependencies"]).lower()
    dev_dependencies = "\n".join(pyproject["dependency-groups"]["dev"]).lower()

    for package in ("httpx", "pyyaml", "rich", "tqdm", "typer"):
        assert package in dependencies
    assert "pre-commit" in dev_dependencies
    assert "ty" in dev_dependencies
    assert "mypy" not in dev_dependencies
    assert Path(".pre-commit-config.yaml").is_file()
    assert Path("Justfile").is_file()
