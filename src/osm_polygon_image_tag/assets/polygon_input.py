from collections.abc import Iterator, Mapping
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


def polygon_rows(path: Path) -> Iterator[dict[str, object]]:
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=4096, columns=list(POLYGON_COLUMNS)):
        yield from batch.to_pylist()


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
