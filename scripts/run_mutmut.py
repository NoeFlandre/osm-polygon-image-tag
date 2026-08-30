"""Run mutmut safely for this project's native geospatial dependencies."""

from types import ModuleType

from mutmut import code_coverage
from mutmut.__main__ import cli


def _keep_loaded_modules(_modules: dict[str, ModuleType]) -> None:
    """Keep native extension modules loaded between mutmut's coverage passes."""


def main() -> None:
    """Run the mutmut command with the project-safe module lifecycle."""

    code_coverage._unload_modules_not_in = _keep_loaded_modules
    cli()


if __name__ == "__main__":
    main()
