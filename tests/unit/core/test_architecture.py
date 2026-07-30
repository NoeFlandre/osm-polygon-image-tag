import ast
from pathlib import Path

PACKAGE_ROOT = Path("src/osm_polygon_image_tag")
ALLOWED_IMPORTS = {
    "core": {"core"},
    "ingest": {"core", "ingest"},
    "artifacts": {"core", "artifacts"},
    "integrations": {"core", "artifacts", "integrations"},
    "runtime": {"core", "ingest", "artifacts", "integrations", "runtime"},
}


def _layer_violations(package_root: Path) -> list[str]:
    violations: list[str] = []
    for module in sorted(package_root.glob("*/*.py")):
        layer = module.parent.name
        if layer not in ALLOWED_IMPORTS:
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules = (node.module,)
            elif isinstance(node, ast.Import):
                imported_modules = tuple(alias.name for alias in node.names)
            else:
                continue
            prefix = "osm_polygon_image_tag."
            for imported_module in imported_modules:
                if not imported_module.startswith(prefix):
                    continue
                imported_layer = imported_module.removeprefix(prefix).split(".", maxsplit=1)[0]
                if imported_layer not in ALLOWED_IMPORTS[layer]:
                    violations.append(f"{module}:{node.lineno} imports {imported_layer}")

    return violations


def test_subpackages_only_import_from_allowed_layers() -> None:
    assert _layer_violations(PACKAGE_ROOT) == []


def test_architecture_guard_checks_ordinary_imports(tmp_path: Path) -> None:
    module = tmp_path / "artifacts" / "escape.py"
    module.parent.mkdir()
    module.write_text("import osm_polygon_image_tag.integrations.huggingface\n")

    assert _layer_violations(tmp_path) == [f"{module}:1 imports integrations"]
