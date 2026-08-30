"""Immutable contracts exchanged with the runtime enrichment worker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from osm_polygon_image_tag.core.manifest import Manifest


@dataclass(frozen=True, slots=True)
class AssetJob:
    manifest: Manifest
    polygon_path: Path


@dataclass(frozen=True, slots=True)
class EnrichmentSummary:
    built: int = 0
    skipped: int = 0
    pending: int = 0
    rows: int = 0
    statuses: dict[str, int] | None = None

    def status_counts(self) -> dict[str, int]:
        return dict(self.statuses or {})
