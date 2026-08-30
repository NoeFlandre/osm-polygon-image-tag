"""Immutable result contracts shared by runtime workflows and the CLI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from osm_polygon_image_tag.runtime.enrichment_types import EnrichmentSummary


@dataclass(frozen=True, slots=True)
class RunSummary:
    processed: int
    built: int
    skipped: int
    accepted_rows: int
    stopped: bool
    enrichment: EnrichmentSummary = field(default_factory=EnrichmentSummary)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VerifySummary:
    checked: int
    valid: int
    invalid: int
    asset_checked: int = 0
    asset_valid: int = 0
    asset_invalid: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
