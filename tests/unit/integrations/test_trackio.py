from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from osm_polygon_image_tag.integrations.trackio import (
    DEFAULT_SPACE_ID,
    metrics_from_statistics,
    publish_trackio,
)


def test_metrics_are_flat_numeric_and_deterministically_named() -> None:
    metrics = metrics_from_statistics(
        {
            "rows": 10,
            "shards": 2,
            "provider_counts": {"image": 7, "wikimedia_commons": 3},
            "assets": {
                "rows": 20,
                "duplicate_assets_removed": 4,
                "direct_urls": 15,
                "provider_counts": {"panoramax": 4},
                "status_counts": {"resolved": 19, "not_found": 1},
            },
            "geography": {"cell_count": 8, "max_cell_count": 4},
        }
    )

    assert list(metrics) == sorted(metrics)
    assert metrics["rows"] == 10
    assert metrics["provider_wikimedia_commons"] == 3
    assert metrics["asset_provider_panoramax"] == 4
    assert metrics["asset_duplicate_rows_removed"] == 4
    assert metrics["asset_status_resolved"] == 19
    assert metrics["geographic_cell_count"] == 8
    assert metrics["direct_image_url_ratio"] == 0.75
    assert all(
        isinstance(value, int | float) and not isinstance(value, bool) for value in metrics.values()
    )


def test_publish_trackio_logs_one_statistics_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    statistics = {"rows": 2, "shards": 1, "assets": {"rows": 3, "direct_urls": 2}}
    path = tmp_path / "statistics" / "dataset-statistics.json"
    path.parent.mkdir()
    path.write_text(json.dumps(statistics, sort_keys=True, separators=(",", ":")) + "\n")
    calls: dict[str, object] = {}

    def init(**kwargs: object) -> None:
        calls["init"] = kwargs

    def log(metrics: dict[str, int | float], *, step: int) -> None:
        calls["metrics"] = metrics
        calls["step"] = step

    def finish() -> None:
        calls["finished"] = True

    monkeypatch.setitem(
        sys.modules,
        "trackio",
        SimpleNamespace(init=init, log=log, finish=finish),
    )
    result = publish_trackio(tmp_path)

    assert result.space_id == DEFAULT_SPACE_ID
    logged_metrics = cast(dict[str, int | float], calls["metrics"])
    init_kwargs = cast(dict[str, object], calls["init"])
    assert result.metric_count == len(logged_metrics)
    assert calls["step"] == 0
    assert calls["finished"] is True
    assert init_kwargs["dataset_id"] == "NoeFlandre/osm-polygon-image-tag"


def test_publish_trackio_requires_optional_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "statistics" / "dataset-statistics.json"
    path.parent.mkdir()
    path.write_text("{}\n")
    monkeypatch.setitem(sys.modules, "trackio", None)

    with pytest.raises(RuntimeError, match="optional dependency"):
        publish_trackio(tmp_path)
