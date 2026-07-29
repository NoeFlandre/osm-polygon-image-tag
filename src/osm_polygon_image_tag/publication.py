import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from huggingface_hub import (
    CommitOperationAdd,
    CommitOperationDelete,
    HfApi,
    hf_hub_download,
)

from osm_polygon_image_tag.catalog import verified_manifests
from osm_polygon_image_tag.errors import PublicationError
from osm_polygon_image_tag.manifest import file_sha256

EXPECTED_REPO = "NoeFlandre/osm-polygon-image-tag"


@dataclass(frozen=True, slots=True)
class PublicationFile:
    local_path: Path
    remote_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class HubCommit:
    repo_id: str
    repo_type: str
    message: str
    files: tuple[PublicationFile, ...]
    deletions: tuple[str, ...] = ()


class Hub(Protocol):
    def commit(self, commit: HubCommit) -> str: ...

    def download(self, repo_id: str, remote_path: str, revision: str) -> bytes: ...


class HuggingFaceHub:
    def __init__(self) -> None:
        self._api = HfApi()

    def commit(self, commit: HubCommit) -> str:
        try:
            result = self._api.create_commit(
                repo_id=commit.repo_id,
                repo_type=commit.repo_type,
                commit_message=commit.message,
                operations=[
                    *[
                        CommitOperationAdd(
                            path_in_repo=item.remote_path,
                            path_or_fileobj=item.local_path,
                        )
                        for item in commit.files
                    ],
                    *[
                        CommitOperationDelete(path_in_repo=remote_path)
                        for remote_path in commit.deletions
                    ],
                ],
            )
            return result.oid
        except Exception as error:
            raise PublicationError("Hugging Face commit failed") from error

    def download(self, repo_id: str, remote_path: str, revision: str) -> bytes:
        try:
            with tempfile.TemporaryDirectory(prefix="osm-image-tag-hf-verify-") as cache:
                downloaded = hf_hub_download(
                    repo_id=repo_id,
                    filename=remote_path,
                    repo_type="dataset",
                    revision=revision,
                    cache_dir=cache,
                    force_download=True,
                )
                return Path(downloaded).read_bytes()
        except Exception as error:
            raise PublicationError(f"Hugging Face verification failed: {remote_path}") from error


@dataclass(frozen=True, slots=True)
class PublicationResult:
    status: str
    commit_id: str
    files: int

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


def _regular_file(root: Path, relative: str) -> Path:
    path = root / relative
    if path.is_symlink():
        raise PublicationError(f"publication artifact must not be a symlink: {relative}")
    if not path.is_file():
        raise PublicationError(f"missing publication artifact: {relative}")
    resolved = path.resolve()
    if root.resolve() not in resolved.parents:
        raise PublicationError(f"publication artifact escapes data root: {relative}")
    return path


def publication_inventory(data_root: Path) -> tuple[PublicationFile, ...]:
    root = data_root.resolve()
    manifests = verified_manifests(root)
    allowed = {"README.md", "statistics/dataset-statistics.json"}
    allowed.update(manifest.output.relative_path for manifest, _ in manifests)
    allowed.update(
        path.relative_to(root).as_posix()
        for path in sorted((root / "manifests").glob("*.manifest.json"))
    )
    internal = {"catalog/catalog.sqlite", "receipts/publication.json"}
    actual: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise PublicationError(f"symlink in data root: {relative}")
        if path.is_file():
            if relative == ".DS_Store":
                continue
            actual.add(relative)
    unexpected = actual - allowed - internal
    if unexpected:
        raise PublicationError(f"unexpected data-root entries: {sorted(unexpected)}")
    files = []
    for relative in sorted(allowed):
        path = _regular_file(root, relative)
        files.append(
            PublicationFile(
                local_path=path,
                remote_path=relative,
                sha256=file_sha256(path),
                size_bytes=path.stat().st_size,
            )
        )
    return tuple(files)


def _inventory_digest(files: tuple[PublicationFile, ...]) -> str:
    payload = [(item.remote_path, item.sha256, item.size_bytes) for item in files]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _write_receipt(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
    try:
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def publish_dataset(
    data_root: Path,
    *,
    confirm_repo: str,
    hub: Hub,
) -> PublicationResult:
    if confirm_repo != EXPECTED_REPO:
        raise PublicationError(f"repository confirmation must equal {EXPECTED_REPO}")
    files = publication_inventory(data_root)
    inventory_digest = _inventory_digest(files)
    receipt_path = data_root / "receipts" / "publication.json"
    receipt: dict[str, object] | None = None
    receipt_files: list[object] = []
    if receipt_path.is_file():
        try:
            candidate = json.loads(receipt_path.read_text())
            if not isinstance(candidate, dict):
                raise ValueError
            candidate_files = candidate.get("files")
            if not isinstance(candidate_files, list):
                raise ValueError
            receipt = dict(candidate)
            receipt_files = candidate_files
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise PublicationError("invalid publication receipt") from error
        if (
            receipt.get("repo_id") == EXPECTED_REPO
            and receipt.get("inventory_sha256") == inventory_digest
        ):
            return PublicationResult("skipped", str(receipt["commit_id"]), len(files))
        if receipt.get("repo_id") != EXPECTED_REPO:
            raise PublicationError("publication receipt repository mismatch")
    previous = {
        str(item["path"]): str(item["sha256"])
        for item in receipt_files
        if isinstance(item, dict) and "path" in item and "sha256" in item
    }
    current = {item.remote_path: item.sha256 for item in files}
    changed = tuple(item for item in files if previous.get(item.remote_path) != item.sha256)
    deleted = tuple(sorted(set(previous) - set(current)))
    commit = HubCommit(
        repo_id=EXPECTED_REPO,
        repo_type="dataset",
        message=f"Publish {len(changed)} changed verified dataset artifacts",
        files=changed,
        deletions=deleted,
    )
    commit_id = hub.commit(commit)
    for item in changed:
        remote = hub.download(EXPECTED_REPO, item.remote_path, commit_id)
        if hashlib.sha256(remote).hexdigest() != item.sha256:
            raise PublicationError(f"remote digest mismatch: {item.remote_path}")
    _write_receipt(
        receipt_path,
        {
            "schema_version": 1,
            "repo_id": EXPECTED_REPO,
            "commit_id": commit_id,
            "inventory_sha256": inventory_digest,
            "files": [
                {
                    "path": item.remote_path,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
                for item in files
            ],
        },
    )
    return PublicationResult("published", commit_id, len(files))
