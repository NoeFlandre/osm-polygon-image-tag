from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pyarrow.parquet as pq

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
    "image",
    "wikimedia_commons",
    "mapillary",
    "panoramax",
    "panoramax_values",
    "kartaview",
    "flickr",
    "bubbleid",
)
REFERENCE_COLUMNS = (
    "image",
    "wikimedia_commons",
    "mapillary",
    "panoramax",
    "panoramax_values",
    "kartaview",
    "flickr",
    "bubbleid",
)
_REFERENCE_SCALAR_COLUMNS = (
    "image",
    "wikimedia_commons",
    "mapillary",
    "kartaview",
    "flickr",
    "bubbleid",
)


def polygon_rows(
    path: Path,
    *,
    columns: Sequence[str] = POLYGON_COLUMNS,
) -> Iterator[dict[str, object]]:
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=4096, columns=list(columns)):
        yield from batch.to_pylist()


def _panoramax_count(value: object, fallback: object) -> int:
    if isinstance(value, Mapping):
        pairs = tuple(value.items())
    elif isinstance(value, list):
        pairs = tuple(
            (pair[0], pair[1])
            for pair in value
            if isinstance(pair, tuple | list) and len(pair) == 2 and pair[0] is not None
        )
    else:
        pairs = ()
    if pairs:
        return sum(isinstance(item, str) and item != "" for _key, item in pairs)
    return int(isinstance(fallback, str) and fallback != "")


def count_polygon_references(path: Path, *, batch_size: int = 4096) -> int:
    """Count normalized references without materializing one mapping per row."""
    parquet = pq.ParquetFile(path)
    total = 0
    for batch in parquet.iter_batches(batch_size=batch_size, columns=list(REFERENCE_COLUMNS)):
        columns = batch.to_pydict()
        total += sum(
            isinstance(value, str) and value != ""
            for name in _REFERENCE_SCALAR_COLUMNS
            for value in columns[name]
        )
        total += sum(
            _panoramax_count(value, fallback)
            for value, fallback in zip(
                columns["panoramax_values"], columns["panoramax"], strict=True
            )
        )
    return total


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
