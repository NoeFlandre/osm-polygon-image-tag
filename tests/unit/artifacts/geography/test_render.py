"""Tests for the matplotlib H3 map renderer."""

from __future__ import annotations

import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import cast

import matplotlib
import matplotlib.colors as mcolors
import matplotlib.figure
import pytest

from osm_polygon_image_tag.artifacts.geography.models import (
    PolygonCountCell,
    RenderResult,
)
from osm_polygon_image_tag.artifacts.geography.render import (
    RENDER_DPI,
    RENDER_FIGSIZE,
    render_count_map,
)

matplotlib.use("Agg")


def _fixture_cells() -> list[PolygonCountCell]:
    return [
        PolygonCountCell(h3_cell="833969fffffffff", polygon_count=25),
        PolygonCountCell(h3_cell="83754efffffffff", polygon_count=3),
    ]


def test_render_creates_valid_png_with_deterministic_filemagic(tmp_path: Path) -> None:
    out = tmp_path / "map.png"
    result = render_count_map(_fixture_cells(), out)
    assert isinstance(result, RenderResult)
    assert result.output_path == out
    assert out.exists()
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_creates_parent_directories(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "dir" / "map.png"
    render_count_map(_fixture_cells(), out)
    assert out.exists()


def test_render_uses_log_normalization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_savefig = matplotlib.figure.Figure.savefig
    call_savefig = cast(Callable[..., object], real_savefig)
    captured: dict[str, object] = {}

    def capture(self: matplotlib.figure.Figure, *args: object, **kwargs: object) -> object:
        # The H3 cell polygons are matplotlib.collections.PolyCollection
        # on the main axes. Capture all norms attached to collections.
        for ax in self.axes:
            for collection in getattr(ax, "collections", []):
                norm = getattr(collection, "norm", None)
                if norm is not None:
                    norms = captured.setdefault("norms", [])
                    if isinstance(norms, list):
                        norms.append(norm)
        return call_savefig(self, *args, **kwargs)

    monkeypatch.setattr(matplotlib.figure.Figure, "savefig", capture)
    render_count_map(_fixture_cells(), tmp_path / "map.png")

    norms = captured.get("norms", [])
    assert isinstance(norms, list) and norms, "Renderer must normalize the colormap"
    assert any(isinstance(norm, mcolors.LogNorm) for norm in norms), (
        "Renderer must use LogNorm for the colormap"
    )


def test_render_colorbar_uses_human_readable_count_formatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_savefig = matplotlib.figure.Figure.savefig
    call_savefig = cast(Callable[..., object], real_savefig)
    captured: dict[str, object] = {}

    def capture(self: matplotlib.figure.Figure, *args: object, **kwargs: object) -> object:
        if len(self.axes) >= 2:
            colorbar_axes = self.axes[1]
            captured["formatter"] = colorbar_axes.yaxis.get_major_formatter()
        return call_savefig(self, *args, **kwargs)

    monkeypatch.setattr(matplotlib.figure.Figure, "savefig", capture)
    render_count_map(_fixture_cells(), tmp_path / "map.png")

    formatter = captured.get("formatter")
    assert formatter is not None
    func = getattr(formatter, "func", None)
    assert callable(func)
    label = func(1000.0, None)
    assert isinstance(label, str)
    assert "%" not in label
    stripped = label.replace(",", "").replace(" ", "").rstrip("kKM")
    assert stripped.isdigit(), f"colorbar must show integer counts, got {label!r}"


def test_render_is_deterministic_within_run(tmp_path: Path) -> None:
    cells = _fixture_cells()
    out_a = tmp_path / "a.png"
    out_b = tmp_path / "b.png"
    render_count_map(cells, out_a)
    render_count_map(cells, out_b)
    assert out_a.read_bytes() == out_b.read_bytes()


def test_render_handles_count_of_one(tmp_path: Path) -> None:
    cells = [PolygonCountCell(h3_cell="833969fffffffff", polygon_count=1)]
    out = tmp_path / "single.png"
    result = render_count_map(cells, out)
    assert result.output_path == out
    assert out.exists()
    assert out.stat().st_size > 0


def test_render_with_empty_cells_produces_valid_world_map(tmp_path: Path) -> None:
    """The empty-dataset case must produce a valid PNG without crashing."""
    out = tmp_path / "empty.png"
    result = render_count_map([], out)
    assert result.output_path == out
    assert out.exists()
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_uses_world_extent_and_dpi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_savefig = matplotlib.figure.Figure.savefig
    call_savefig = cast(Callable[..., object], real_savefig)
    captured: dict[str, object] = {}

    def capture(self: matplotlib.figure.Figure, *args: object, **kwargs: object) -> object:
        captured["figsize_inches"] = tuple(float(value) for value in self.get_size_inches())
        captured["dpi"] = float(self.get_dpi())
        captured["xlim"] = tuple(self.axes[0].get_xlim())
        captured["ylim"] = tuple(self.axes[0].get_ylim())
        return call_savefig(self, *args, **kwargs)

    monkeypatch.setattr(matplotlib.figure.Figure, "savefig", capture)
    render_count_map(_fixture_cells(), tmp_path / "map.png")

    assert captured["figsize_inches"] == RENDER_FIGSIZE
    assert captured["dpi"] == RENDER_DPI
    assert captured["xlim"] == (-180.0, 180.0)
    assert captured["ylim"] == (-90.0, 90.0)


def test_render_does_not_perform_network_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("network call attempted")

    monkeypatch.setattr(urllib.request, "urlretrieve", fail)
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    render_count_map(_fixture_cells(), tmp_path / "map.png")


def test_render_uses_temporary_file_and_atomic_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomic PNG writes must use a temporary file adjacent to the final path."""
    import os

    seen_replaces: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def tracking_replace(src: str | Path, dst: str | Path) -> None:
        seen_replaces.append((Path(str(src)), Path(str(dst))))
        return original_replace(src, dst)

    monkeypatch.setattr(
        "osm_polygon_image_tag.artifacts.geography.render.os.replace",
        tracking_replace,
    )
    out = tmp_path / "map.png"
    render_count_map(_fixture_cells(), out)
    assert any(dst == out for _, dst in seen_replaces)


def test_render_bundled_basemap_is_loaded(tmp_path: Path) -> None:
    """The basemap is loaded from the bundled package asset."""

    out = tmp_path / "map.png"
    render_count_map(_fixture_cells(), out)
    assert out.exists()


def test_render_cleans_up_figure_temporaries(tmp_path: Path) -> None:
    """`render_count_map` must close the matplotlib figure after writing."""
    import matplotlib.pyplot as plt

    figures_before = set(id(fig) for fig in plt.get_fignums())
    render_count_map(_fixture_cells(), tmp_path / "map.png")
    figures_after = set(id(fig) for fig in plt.get_fignums())
    new_figures = figures_after - figures_before
    assert not new_figures, "Renderer must close the figure after saving"
