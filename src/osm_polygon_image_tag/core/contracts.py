"""Canonical image-reference keys shared across the dataset pipeline."""

IMAGE_REFERENCE_KEYS: tuple[str, ...] = (
    "image",
    "wikimedia_commons",
    "mapillary",
    "panoramax",
    "kartaview",
    "flickr",
    "bubbleid",
)
PANORAMAX_VALUES_COLUMN = "panoramax_values"
REFERENCE_COLUMNS: tuple[str, ...] = (
    "image",
    "wikimedia_commons",
    "mapillary",
    "panoramax",
    PANORAMAX_VALUES_COLUMN,
    "kartaview",
    "flickr",
    "bubbleid",
)
SCALAR_REFERENCE_COLUMNS: tuple[str, ...] = tuple(
    key for key in IMAGE_REFERENCE_KEYS if key != "panoramax"
)

__all__ = [
    "IMAGE_REFERENCE_KEYS",
    "PANORAMAX_VALUES_COLUMN",
    "REFERENCE_COLUMNS",
    "SCALAR_REFERENCE_COLUMNS",
]
