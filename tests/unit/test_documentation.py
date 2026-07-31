"""Contract tests for the public documentation site and Pages workflow."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_mkdocs_site_contract() -> None:
    config = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")

    assert "site_name: OSM Polygon Image Tag" in config
    assert "site_url: https://noeflandre.github.io/osm-polygon-image-tag/" in config
    assert "name: material" in config
    for page in (
        "index.md",
        "getting-started.md",
        "cli.md",
        "architecture.md",
        "data-contract.md",
        "operations.md",
        "development.md",
    ):
        assert page in config


def test_pages_workflow_contract() -> None:
    workflow = (ROOT / ".github/workflows/docs.yml").read_text(encoding="utf-8")

    assert "branches: [main]" in workflow
    assert "workflow_dispatch:" in workflow
    assert "contents: read" in workflow
    assert "pages: write" in workflow
    assert "id-token: write" in workflow
    assert "uv sync --locked --dev" in workflow
    assert "uv run mkdocs build --strict --site-dir site" in workflow
    assert "actions/upload-pages-artifact@" in workflow
    assert "actions/deploy-pages@" in workflow

    action_refs = re.findall(r"uses:\s+[^\s@]+@([0-9a-f]{40})", workflow)
    assert action_refs
    assert len(action_refs) == workflow.count("uses:")
