"""Run mutmut safely for this project's native geospatial dependencies."""

import importlib
import sys
from pathlib import Path
from types import ModuleType

from mutmut import code_coverage
from mutmut.__main__ import cli

_PROJECT_MODULE_PREFIX = "osm_polygon_image_tag"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MUTANTS_ROOT = _PROJECT_ROOT / "mutants"
_TESTS_ROOT = _PROJECT_ROOT / "tests"


def _is_local_module(module: ModuleType) -> bool:
    filename = getattr(module, "__file__", None)
    if not isinstance(filename, str):
        return False
    path = Path(filename).resolve()
    return _TESTS_ROOT in path.parents or _MUTANTS_ROOT in path.parents


def _keep_loaded_modules(_modules: dict[str, ModuleType]) -> None:
    """Reload Python modules while keeping native extensions available."""
    for name, module in list(sys.modules.items()):
        is_project_module = name == _PROJECT_MODULE_PREFIX or name.startswith(
            f"{_PROJECT_MODULE_PREFIX}."
        )
        if name == "mutmut.code_coverage":
            continue
        if is_project_module:
            sys.modules.pop(name, None)
            continue
        if name == "tests" or name.startswith("tests."):
            sys.modules.pop(name, None)
            continue
        if _is_local_module(module):
            sys.modules.pop(name, None)
    importlib.invalidate_caches()


def main() -> None:
    """Run the mutmut command with the project-safe module lifecycle."""

    setattr(code_coverage, "_unload_modules_not_in", _keep_loaded_modules)  # noqa: B010
    cli()


if __name__ == "__main__":
    main()
