"""Optional Trackio publisher for the published dataset statistics.

Trackio is deliberately imported lazily.  Running the extraction pipeline or
the normal Hugging Face publisher must not require the optional Trackio
dependency or create a network side effect.  Call :func:`publish_trackio`
after metadata generation when a public metrics Space should be refreshed.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

DEFAULT_PROJECT = "osm-polygon-image-tag"
DEFAULT_SPACE_ID = "NoeFlandre/osm-polygon-image-tag-trackio"
DEFAULT_DATASET_ID = "NoeFlandre/osm-polygon-image-tag-trackio-data"
STATISTICS_RELATIVE_PATH = Path("statistics/dataset-statistics.json")


@dataclass(frozen=True, slots=True)
class TrackioPublication:
    """Stable summary of a Trackio metrics publication."""

    project: str
    space_id: str
    statistics_sha256: str
    metric_count: int

    def to_dict(self) -> dict[str, str | int]:
        return {
            "project": self.project,
            "space_id": self.space_id,
            "statistics_sha256": self.statistics_sha256,
            "metric_count": self.metric_count,
        }


def _number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    return None


def _add_metric(metrics: dict[str, int | float], name: str, value: object) -> None:
    number = _number(value)
    if number is not None:
        metrics[name] = number


def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def metrics_from_statistics(statistics: Mapping[str, object]) -> dict[str, int | float]:
    """Extract a compact, numeric, deterministic metric set from dataset stats.

    Trackio charts are most useful when metric names are stable across runs.
    Nested provider and status counts are therefore flattened with explicit
    ``polygon_`` and ``asset_`` prefixes rather than logging arbitrary JSON.
    """
    metrics: dict[str, int | float] = {}
    for source, prefix in ((statistics, ""), (_mapping(statistics.get("assets")), "asset_")):
        metrics.update(_source_metrics(source, prefix))
    metrics.update(_geography_metrics(_mapping(statistics.get("geography"))))
    metrics.update(_derived_metrics(statistics))
    return dict(sorted(metrics.items()))


def _source_metrics(source: Mapping[str, object], prefix: str) -> dict[str, int | float]:
    metrics: dict[str, int | float] = {}
    fields = {
        "rows": f"{prefix}rows",
        "shards": f"{prefix}shards",
        "duplicate_observations": f"{prefix}duplicate_rows",
        "duplicate_observations_removed": f"{prefix}duplicate_rows_removed",
        "duplicate_assets": f"{prefix}duplicate_rows",
        "duplicate_assets_removed": f"{prefix}duplicate_rows_removed",
        "direct_urls": f"{prefix}direct_image_urls",
        "stable_direct_urls": f"{prefix}stable_direct_image_urls",
        "page_urls": f"{prefix}page_urls",
        "licensed_assets": f"{prefix}licensed_assets",
        "pending_retries": f"{prefix}pending_retries",
        "output_bytes": f"{prefix}output_bytes",
        "source_bytes": f"{prefix}source_bytes",
    }
    for field, name in fields.items():
        _add_metric(metrics, name, source.get(field))
    for field, field_prefix in (
        ("provider_counts", f"{prefix}provider_"),
        ("status_counts", f"{prefix}status_"),
    ):
        metrics.update(_nested_metrics(source.get(field), field_prefix))
    return metrics


def _nested_metrics(value: object, prefix: str) -> dict[str, int | float]:
    metrics: dict[str, int | float] = {}
    for key, item in sorted(_mapping(value).items()):
        safe_key = str(key).replace("-", "_").replace(" ", "_")
        _add_metric(metrics, f"{prefix}{safe_key}", item)
    return metrics


def _geography_metrics(geography: Mapping[str, object]) -> dict[str, int | float]:
    metrics: dict[str, int | float] = {}
    for field, name in (
        ("cell_count", "geographic_cell_count"),
        ("polygon_rows", "geographic_polygon_rows"),
        ("input_shard_count", "geographic_input_shards"),
        ("min_cell_count", "geographic_min_cell_count"),
        ("max_cell_count", "geographic_max_cell_count"),
    ):
        _add_metric(metrics, name, geography.get(field))
    return metrics


def _derived_metrics(statistics: Mapping[str, object]) -> dict[str, int | float]:
    metrics: dict[str, int | float] = {}
    assets = _mapping(statistics.get("assets"))
    polygon_rows = _number(statistics.get("rows"))
    asset_rows = _number(assets.get("rows"))
    direct_urls = _number(assets.get("direct_urls"))
    if polygon_rows:
        metrics["asset_rows_per_polygon"] = float(asset_rows or 0) / polygon_rows
    if asset_rows:
        metrics["direct_image_url_ratio"] = float(direct_urls or 0) / asset_rows
    return metrics


def _load_statistics(data_root: Path) -> tuple[dict[str, object], str]:
    path = data_root / STATISTICS_RELATIVE_PATH
    try:
        content = path.read_bytes()
        value = json.loads(content)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read dataset statistics: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"dataset statistics must be a JSON object: {path}")
    return dict(value), hashlib.sha256(content).hexdigest()


def publish_trackio(
    data_root: Path,
    *,
    project: str = DEFAULT_PROJECT,
    space_id: str = DEFAULT_SPACE_ID,
    dataset_id: str = DEFAULT_DATASET_ID,
    token: str | None = None,
) -> TrackioPublication:
    """Log one immutable statistics snapshot to a Trackio Space.

    The optional ``trackio`` package is imported only when this function is
    called.  Trackio creates the Space when it does not exist (with a token
    that has permission to create Spaces) and updates it on subsequent calls.
    """
    statistics, digest = _load_statistics(data_root)
    metrics = metrics_from_statistics(statistics)
    try:
        trackio = importlib.import_module("trackio")
    except ImportError as error:
        raise RuntimeError(
            "Trackio publishing requires the optional dependency; "
            "run `uv run --with trackio python -m "
            "osm_polygon_image_tag.integrations.trackio`"
        ) from error

    config: dict[str, Any] = {
        "dataset_id": dataset_id,
        "dataset_statistics_sha256": digest,
        "dataset_statistics_path": STATISTICS_RELATIVE_PATH.as_posix(),
        "source_repository": "https://github.com/NoeFlandre/osm-polygon-image-tag",
        "dataset_url": f"https://huggingface.co/datasets/{dataset_id}",
    }
    init_kwargs: dict[str, Any] = {
        "project": project,
        "name": f"dataset-{digest[:12]}",
        "config": config,
    }
    if token is not None:
        init_kwargs["token"] = token
    trackio.init(**init_kwargs)
    try:
        trackio.log(metrics, step=0)
    finally:
        trackio.finish()
    trackio.sync(
        project=project,
        space_id=space_id,
        sdk="static",
        dataset_id=dataset_id,
        force=True,
    )
    return TrackioPublication(project, space_id, digest, len(metrics))


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI command
    import argparse

    parser = argparse.ArgumentParser(description="Publish dataset statistics to Trackio")
    parser.add_argument("data_root", type=Path)
    parser.add_argument("--space-id", default=DEFAULT_SPACE_ID)
    parser.add_argument("--dataset-id", default=DEFAULT_DATASET_ID)
    args = parser.parse_args()
    print(
        json.dumps(
            publish_trackio(
                args.data_root,
                space_id=args.space_id,
                dataset_id=args.dataset_id,
            ).to_dict(),
            sort_keys=True,
        )
    )
