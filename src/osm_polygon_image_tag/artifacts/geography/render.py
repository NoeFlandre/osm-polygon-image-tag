"""Static PNG renderer for the H3 polygon density map.

The renderer is faithful to the Wikidata project's
``geographic_text_density.png`` visual contract:

- Matplotlib Agg backend.
- 1600x800 figure at 100 DPI.
- World extent ``[-180, 180] x [-90, 90]``.
- Light blue ocean with optional Natural Earth landmasses.
- H3 cell polygons filled with the ``magma`` colormap on a logarithmic
  ``LogNorm`` scale.
- Dark, thin cell edges.
- Count colorbar with human-readable ticks (``1``, ``1k``, ``1M``).
- Atomic PNG writes via temporary file + ``os.replace``.
- Antimeridian-safe cell rendering via the H3 segmentation helper.

The renderer is pure: it does not perform any network I/O and does not
touch the data root outside the supplied output path.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick

from .basemap import draw_landmasses, load_land_basemap
from .h3 import cell_rings
from .models import GeographicMapError, PolygonCountCell, RenderResult

LOGGER = logging.getLogger(__name__)

# Canonical remote path for the geographic coverage PNG. Do not scatter
# this string across the codebase.
GEOGRAPHIC_PNG_RELATIVE: str = "assets/geographic_polygon_density.png"

# Visualization constants matching the Wikidata project.
RENDER_FIGSIZE: tuple[float, float] = (16.0, 8.0)
RENDER_DPI: int = 100
_OCEAN_COLOR: str = "#cfe2f3"
_COUNT_COLORMAP_NAME: str = "magma"
_COUNT_ALPHA: float = 0.95
_COUNT_EDGE_COLOR: str = "#333333"
_COUNT_EDGE_WIDTH: float = 0.2


def _format_count_tick(value: float, _position: int | None = None) -> str:
    """Format a polygon-count colorbar value as a human-readable integer label."""
    count = round(value)
    if count < 1_000:
        return str(count)
    if count < 1_000_000:
        thousands = count / 1_000.0
        return f"{thousands:.0f}k" if thousands.is_integer() else f"{thousands:.1f}k"
    millions = count / 1_000_000.0
    return f"{millions:.1f}M"


def _coerce_cells(cells: Sequence[PolygonCountCell]) -> list[PolygonCountCell]:
    """Sort and validate the input cells deterministically."""
    coerced: list[PolygonCountCell] = []
    seen: set[str] = set()
    for cell in cells:
        if not isinstance(cell, PolygonCountCell):
            raise GeographicMapError(
                f"All cells must be PolygonCountCell instances; got {type(cell).__name__}."
            )
        if cell.h3_cell in seen:
            raise GeographicMapError(f"Duplicate H3 cell id supplied to renderer: {cell.h3_cell}")
        seen.add(cell.h3_cell)
        coerced.append(cell)
    coerced.sort(key=lambda entry: entry.h3_cell)
    return coerced


def _build_caption(
    cells: Sequence[PolygonCountCell],
    *,
    h3_resolution: int,
    total_polygons: int,
) -> str:
    """Render the deterministic caption used on the map."""
    return (
        "Geographic OSM Polygon Density. Each H3 cell contains the raw count of "
        f"finalized `polygons` rows whose geometry centroid falls into the cell. "
        f"Polygons are assigned by their geometry centroid at H3 resolution "
        f"{h3_resolution}. {total_polygons:,} supplied polygon rows across "
        f"{len(cells):,} H3 cells. Image rows and polygon-image links are not "
        "counted in this map. Colour uses a logarithmic scale."
    )


def _atomic_save_png(fig: Any, output_path: Path) -> None:
    """Save ``fig`` to ``output_path`` via a temporary file then atomic rename."""
    with tempfile.NamedTemporaryFile(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=str(output_path.parent),
        delete=False,
    ) as tmp_file:
        tmp_path = Path(tmp_file.name)
    try:
        fig.savefig(
            str(tmp_path),
            format="png",
            facecolor="white",
            metadata={"Software": "osm-polygon-image-tag"},
        )
        os.replace(tmp_path, output_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _draw_cell(
    ax: Any,
    cell: PolygonCountCell,
    *,
    cmap: mcolors.Colormap,
    norm: mcolors.LogNorm,
) -> None:
    """Draw a single H3 cell on ``ax`` for the polygon count map."""
    safe_count = max(int(cell.polygon_count), 1)
    facecolor = cmap(norm(safe_count))
    for ring in cell_rings(cell.h3_cell):
        patch = mpatches.Polygon(
            ring,
            closed=True,
            facecolor=facecolor,
            edgecolor=_COUNT_EDGE_COLOR,
            linewidth=_COUNT_EDGE_WIDTH,
            alpha=_COUNT_ALPHA,
            zorder=3,
        )
        ax.add_patch(patch)


def _init_axes(ax: Any) -> None:
    """Apply the shared world-extent styling."""
    ax.set_facecolor(_OCEAN_COLOR)
    ax.set_xlim(-180.0, 180.0)
    ax.set_ylim(-90.0, 90.0)
    ax.set_xticks(range(-180, 181, 30))
    ax.set_yticks(range(-90, 91, 30))
    ax.grid(True, color="#ffffff", linewidth=0.3, alpha=0.4)
    ax.tick_params(colors="#666666", labelsize=7)
    ax.set_aspect("equal", adjustable="box")


def render_count_map(
    cells: Iterable[PolygonCountCell],
    output_path: Path,
    *,
    title: str = "Geographic OSM Polygon Density",
    h3_resolution: int = 3,
    use_basemap: bool = True,
) -> RenderResult:
    """Render the polygon density PNG to ``output_path`` with deterministic output.

    The function accepts the input iterable lazily, but coerces it to a
    list internally so the rendered output is fully deterministic.
    """
    coerced = _coerce_cells(list(cells))
    if not coerced:
        # The empty case still produces a valid deterministic world map.
        return _render_empty_world(output_path, h3_resolution=h3_resolution)
    return _render_populated_world(
        coerced,
        output_path,
        title=title,
        h3_resolution=h3_resolution,
        use_basemap=use_basemap,
    )


def _render_populated_world(
    coerced: Sequence[PolygonCountCell],
    output_path: Path,
    *,
    title: str,
    h3_resolution: int,
    use_basemap: bool,
) -> RenderResult:
    total_polygons = sum(int(cell.polygon_count) for cell in coerced)
    caption = _build_caption(coerced, h3_resolution=h3_resolution, total_polygons=total_polygons)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=RENDER_FIGSIZE, dpi=RENDER_DPI)
    try:
        fig.set_facecolor("white")
        _init_axes(ax)
        if use_basemap:
            _draw_land_basemap(ax)

        counts = [int(cell.polygon_count) for cell in coerced]
        minimum = max(min(counts), 1)
        maximum = max(max(counts), minimum + 1)
        cmap = plt.get_cmap(_COUNT_COLORMAP_NAME)
        norm = mcolors.LogNorm(vmin=minimum, vmax=maximum)

        for cell in coerced:
            _draw_cell(ax, cell, cmap=cmap, norm=norm)

        fig.suptitle(title, fontsize=14, color="#222222", y=0.98)
        fig.text(
            0.5,
            0.02,
            caption,
            ha="center",
            va="bottom",
            fontsize=7,
            color="#444444",
            wrap=True,
        )

        sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])
        colorbar = fig.colorbar(sm, ax=ax, fraction=0.025, pad=0.02)
        colorbar.set_label("Polygons per H3 cell", fontsize=8, color="#333333")
        colorbar.ax.yaxis.set_major_formatter(mtick.FuncFormatter(_format_count_tick))
        colorbar.ax.tick_params(labelsize=7)

        fig.tight_layout(rect=(0, 0.06, 1, 0.95))
        _atomic_save_png(fig, output_path)
    finally:
        plt.close(fig)

    LOGGER.info("Wrote geographic polygon density map to %s", output_path)
    return RenderResult(output_path=output_path, caption=caption)


def _draw_land_basemap(ax: Any) -> None:
    land_features = load_land_basemap()
    if land_features:
        draw_landmasses(ax, land_features)


def _render_empty_world(output_path: Path, *, h3_resolution: int) -> RenderResult:
    """Render a deterministic empty-world PNG with a factual empty caption."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    caption = (
        "Geographic OSM Polygon Density. 0 finalized polygon rows. Colour scale "
        "is logarithmic for populated cells; an empty dataset renders the world "
        f"extent [-180, 180] x [-90, 90] at H3 resolution {h3_resolution} without "
        "any H3 cells. Polygons are assigned by their geometry centroid; "
        "The supplied polygon table is empty, so no H3 cells are shown."
    )
    fig, ax = plt.subplots(figsize=RENDER_FIGSIZE, dpi=RENDER_DPI)
    try:
        fig.set_facecolor("white")
        _init_axes(ax)
        land_features = load_land_basemap()
        if land_features:
            draw_landmasses(ax, land_features)
        fig.suptitle("Geographic OSM Polygon Density", fontsize=14, color="#222222", y=0.98)
        fig.text(
            0.5,
            0.02,
            caption,
            ha="center",
            va="bottom",
            fontsize=7,
            color="#444444",
            wrap=True,
        )
        fig.tight_layout(rect=(0, 0.06, 1, 0.95))
        _atomic_save_png(fig, output_path)
    finally:
        plt.close(fig)
    LOGGER.info("Wrote empty geographic density map to %s", output_path)
    return RenderResult(output_path=output_path, caption=caption)


__all__ = [
    "GEOGRAPHIC_PNG_RELATIVE",
    "RENDER_DPI",
    "RENDER_FIGSIZE",
    "render_count_map",
]
