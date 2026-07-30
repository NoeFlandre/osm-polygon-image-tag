"""Local publication planning: inventory, commits, receipts, and reconciliation.

This module owns the in-data-root side of Hugging Face publication: deciding
which artifacts are eligible, building an upload transaction, verifying the
remote state, and writing the local receipt. The actual Hugging Face SDK
lives in ``osm_polygon_image_tag.integrations.huggingface``; this module
only depends on the structural ``Hub`` protocol.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from osm_polygon_image_tag.artifacts.publication_inventory import publication_inventory
from osm_polygon_image_tag.artifacts.publication_types import Hub, HubCommit, PublicationFile
from osm_polygon_image_tag.core.errors import PublicationError

EXPECTED_REPO = "NoeFlandre/osm-polygon-image-tag"


@dataclass(frozen=True, slots=True)
class PublicationResult:
    status: str
    commit_id: str
    files: int

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


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
    previous_entries: list[tuple[str, str]] = []
    for entry in receipt_files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        digest = entry.get("sha256")
        if isinstance(path, str) and isinstance(digest, str):
            previous_entries.append((path, digest))
    previous = dict(previous_entries)
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
