from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pyarrow.parquet as pq

from osm_polygon_image_tag.assets.references import _iter_pairs
from osm_polygon_image_tag.core.contracts import (
    PANORAMAX_VALUES_COLUMN,
    REFERENCE_COLUMNS,
    SCALAR_REFERENCE_COLUMNS,
)

POLYGON_COLUMNS = (
    "source_pbf",
    "osm_type",
    "osm_id",
    "osm_version",
    "bbox_min_lon",
    "bbox_min_lat",
    "bbox_max_lon",
    "bbox_max_lat",
    "tags",
    *REFERENCE_COLUMNS,
)
_REFERENCE_SCALAR_COLUMNS = SCALAR_REFERENCE_COLUMNS


def polygon_rows(
    path: Path,
    *,
    columns: Sequence[str] = POLYGON_COLUMNS,
) -> Iterator[dict[str, object]]:
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=4096, columns=list(columns)):
        yield from batch.to_pylist()


def _panoramax_count(value: object, fallback: object) -> int:
    pairs = _panoramax_pairs(value)
    if pairs:
        return sum(isinstance(item, str) and item != "" for _key, item in pairs)
    return int(isinstance(fallback, str) and fallback != "")


def _panoramax_pairs(value: object) -> tuple[tuple[object, object], ...]:
    if isinstance(value, Mapping):
        return tuple(value.items())
    if not isinstance(value, list):
        return ()
    return tuple(_iter_pairs(value))


def count_polygon_references(path: Path, *, batch_size: int = 4096) -> int:
    """Count normalized references without materializing one mapping per row."""
    parquet = pq.ParquetFile(path)
    total = 0
    for batch in parquet.iter_batches(batch_size=batch_size, columns=list(REFERENCE_COLUMNS)):
        columns = batch.to_pydict()
        total += _count_scalar_references(columns)
        total += _count_panoramax_references(columns)
    return total


def _count_scalar_references(columns: Mapping[str, list[object]]) -> int:
    return sum(
        isinstance(value, str) and value != ""
        for name in _REFERENCE_SCALAR_COLUMNS
        for value in columns[name]
    )


def _count_panoramax_references(columns: Mapping[str, list[object]]) -> int:
    return sum(
        _panoramax_count(value, fallback)
        for value, fallback in zip(
            columns[PANORAMAX_VALUES_COLUMN], columns["panoramax"], strict=True
        )
    )


def polygon_bbox(row: Mapping[str, object]) -> tuple[float, float, float, float]:
    def coordinate(name: str) -> float:
        value = row[name]
        if not isinstance(value, int | float):
            raise TypeError(f"invalid polygon coordinate: {name}")
        return float(value)

    return (
        coordinate("bbox_min_lon"),
        coordinate("bbox_min_lat"),
        coordinate("bbox_max_lon"),
        coordinate("bbox_max_lat"),
    )
