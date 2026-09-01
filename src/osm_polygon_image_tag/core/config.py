import os
from dataclasses import dataclass
from pathlib import Path

from osm_polygon_image_tag.core.errors import ConfigurationError

DATA_ROOT_ENVIRONMENT_VARIABLE = "OSM_POLYGON_IMAGE_TAG_DATA_ROOT"
DEFAULT_DATA_ROOT = Path("/Volumes/Seagate M3/projects/osm-polygon-image-tag")


def resolve_data_root(data_root: Path | None) -> Path:
    """Resolve an explicit root or the mounted external project root."""
    if data_root is not None:
        return data_root

    configured = os.environ.get(DATA_ROOT_ENVIRONMENT_VARIABLE)
    if configured:
        return Path(configured).expanduser()

    if DEFAULT_DATA_ROOT.parent.is_dir():
        return DEFAULT_DATA_ROOT

    raise ConfigurationError(
        f"--data-root is required when external storage is unavailable; "
        f"pass --data-root or set {DATA_ROOT_ENVIRONMENT_VARIABLE}"
    )


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


@dataclass(frozen=True, slots=True)
class PipelinePaths:
    source_root: Path
    data_root: Path

    @classmethod
    def build(cls, *, source_root: Path, data_root: Path) -> "PipelinePaths":
        if source_root.is_symlink():
            raise ConfigurationError("source root must not be a symlink")
        try:
            canonical_source = source_root.expanduser().resolve(strict=True)
        except OSError as error:
            raise ConfigurationError(f"source root is unavailable: {source_root}") from error
        if not canonical_source.is_dir():
            raise ConfigurationError("source root must be a directory")

        canonical_data = data_root.expanduser().resolve(strict=False)
        if _overlaps(canonical_source, canonical_data):
            raise ConfigurationError("source root and data root must not overlap")
        return cls(source_root=canonical_source, data_root=canonical_data)
