from importlib.metadata import metadata, version
from pathlib import Path
from tomllib import loads
from types import ModuleType, SimpleNamespace

import pytest

import osm_polygon_image_tag
from scripts import run_mutmut as mutation_runner


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


def test_qa_recipe_has_the_required_deterministic_stage_order() -> None:
    justfile = Path("Justfile").read_text(encoding="utf-8")
    qa_body = justfile.split("\nqa:\n", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
    stage_calls = [
        line.strip() for line in qa_body.splitlines() if line.strip().startswith("just ")
    ]

    assert stage_calls == [
        "just baseline",
        "just ruff",
        "just ty",
        "just tests",
        "just acceptance",
        "just architecture",
        "just crap-report",
        "just mutation",
        "just smoke",
        "just diff-review",
    ]


def test_mutation_recipe_requires_every_mutant_to_be_killed() -> None:
    justfile = Path("Justfile").read_text(encoding="utf-8")
    mutation_body = justfile.split("\nmutation:\n", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]

    assert "uv run python scripts/run_mutmut.py run --max-children 2" in mutation_body
    assert "mutmut results --all=true" in mutation_body
    assert '$NF != "killed"' in mutation_body
    assert Path("scripts/run_mutmut.py").is_file()


def test_mutation_configuration_covers_all_covered_source() -> None:
    pyproject = loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    mutation = pyproject["tool"]["mutmut"]

    assert mutation["source_paths"] == ["src"]
    assert mutation["mutate_only_covered_lines"] is True
    assert "only_mutate" not in mutation
    assert "pytest_add_cli_args_test_selection" not in mutation
    assert set(mutation["also_copy"]) >= {
        ".github",
        ".pre-commit-config.yaml",
        ".dockerignore",
        "CONTRIBUTING.md",
        "Dockerfile",
        "Justfile",
        "README.md",
        "citation.cff",
        "mkdocs.yml",
        "scripts",
    }


def test_mutation_runner_reloads_project_modules_but_keeps_native_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_module = ModuleType("osm_polygon_image_tag.synthetic")
    native_module = ModuleType("native_extension.synthetic")
    test_namespace = ModuleType("tests.synthetic")
    loaded_modules = {
        module.__name__: module for module in (project_module, native_module, test_namespace)
    }
    with monkeypatch.context() as context:
        context.setattr(mutation_runner, "sys", SimpleNamespace(modules=loaded_modules))
        mutation_runner._keep_loaded_modules({project_module.__name__: project_module})

    assert loaded_modules == {native_module.__name__: native_module}
