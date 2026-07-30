import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from osm_polygon_image_tag.core.errors import ConfigurationError


@dataclass(frozen=True, slots=True, order=True)
class PbfSource:
    relative_path: PurePosixPath
    absolute_path: Path
    size_bytes: int


def discover_pbfs(source_root: Path) -> tuple[PbfSource, ...]:
    root = source_root.resolve(strict=True)
    discovered: list[PbfSource] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(directory)
        for name in sorted((*directory_names, *file_names)):
            candidate = current / name
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ConfigurationError(f"source tree contains a symlink: {candidate}")

        directory_names.sort()
        for name in sorted(file_names):
            if not name.endswith(".osm.pbf"):
                continue
            candidate = current / name
            details = candidate.stat(follow_symlinks=False)
            if not stat.S_ISREG(details.st_mode):
                raise ConfigurationError(f"PBF entry is not a regular file: {candidate}")
            discovered.append(
                PbfSource(
                    relative_path=PurePosixPath(candidate.relative_to(root).as_posix()),
                    absolute_path=candidate.resolve(strict=True),
                    size_bytes=details.st_size,
                )
            )
    return tuple(sorted(discovered))
