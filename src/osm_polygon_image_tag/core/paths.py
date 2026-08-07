"""Safety checks for manifest-relative paths in the managed data root."""

from pathlib import Path


def resolve_managed_output(
    data_root: Path,
    relative_path: str,
    *,
    label: str = "managed output",
) -> Path:
    """Resolve an output path while rejecting escapes and symlink components."""
    root = data_root.resolve()
    relative = Path(relative_path)
    candidate = root / relative
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"{label} is a symlink: {relative_path}")
    resolved = candidate.resolve()
    if root not in resolved.parents:
        raise ValueError(f"{label} escapes data root: {relative_path}")
    return resolved
