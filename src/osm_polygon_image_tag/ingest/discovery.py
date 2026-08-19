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
        _reject_symlink_entries(current, directory_names, file_names)
        directory_names.sort()
        discovered.extend(_discover_directory(root, current, file_names))
    return tuple(sorted(discovered))


def _reject_symlink_entries(
    directory: Path, directory_names: list[str], file_names: list[str]
) -> None:
    for name in sorted((*directory_names, *file_names)):
        candidate = directory / name
        if stat.S_ISLNK(candidate.lstat().st_mode):
            raise ConfigurationError(f"source tree contains a symlink: {candidate}")


def _discover_directory(root: Path, directory: Path, file_names: list[str]) -> list[PbfSource]:
    sources: list[PbfSource] = []
    for name in sorted(file_names):
        if not name.endswith(".osm.pbf"):
            continue
        candidate = directory / name
        details = candidate.stat(follow_symlinks=False)
        if not stat.S_ISREG(details.st_mode):
            raise ConfigurationError(f"PBF entry is not a regular file: {candidate}")
        sources.append(
            PbfSource(
                relative_path=PurePosixPath(candidate.relative_to(root).as_posix()),
                absolute_path=candidate.resolve(strict=True),
                size_bytes=details.st_size,
            )
        )
    return sources
