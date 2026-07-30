from dataclasses import dataclass
from pathlib import Path

from osm_polygon_image_tag.core.errors import ConfigurationError


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
