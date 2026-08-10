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
from dataclasses import asdict, dataclass
from pathlib import Path

from osm_polygon_image_tag.artifacts.publication_inventory import publication_inventory
from osm_polygon_image_tag.artifacts.publication_types import Hub, HubCommit, PublicationFile
from osm_polygon_image_tag.core.atomic import atomic_write_bytes
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
    content = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()
    atomic_write_bytes(path, content)


def _load_receipt(path: Path) -> tuple[dict[str, object] | None, list[object]]:
    if not path.is_file():
        return None, []
    try:
        candidate = json.loads(path.read_text())
        if not isinstance(candidate, dict):
            raise ValueError
        candidate_files = candidate.get("files")
        if not isinstance(candidate_files, list):
            raise ValueError
        return dict(candidate), candidate_files
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise PublicationError("invalid publication receipt") from error


def _resume_result(
    receipt: dict[str, object] | None,
    *,
    inventory_digest: str,
    file_count: int,
) -> PublicationResult | None:
    if receipt is None:
        return None
    if (
        receipt.get("repo_id") == EXPECTED_REPO
        and receipt.get("inventory_sha256") == inventory_digest
    ):
        return PublicationResult("skipped", str(receipt["commit_id"]), file_count)
    if receipt.get("repo_id") != EXPECTED_REPO:
        raise PublicationError("publication receipt repository mismatch")
    return None


def _previous_file_digests(receipt_files: list[object]) -> dict[str, str]:
    previous_entries: list[tuple[str, str]] = []
    for entry in receipt_files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        digest = entry.get("sha256")
        if isinstance(path, str) and isinstance(digest, str):
            previous_entries.append((path, digest))
    return dict(previous_entries)


def _plan_commit(files: tuple[PublicationFile, ...], previous: dict[str, str]) -> HubCommit:
    current = {item.remote_path: item.sha256 for item in files}
    changed = tuple(item for item in files if previous.get(item.remote_path) != item.sha256)
    deleted = tuple(sorted(set(previous) - set(current)))
    return HubCommit(
        repo_id=EXPECTED_REPO,
        repo_type="dataset",
        message=f"Publish {len(changed)} changed verified dataset artifacts",
        files=changed,
        deletions=deleted,
    )


def _verify_remote_files(hub: Hub, commit_id: str, files: tuple[PublicationFile, ...]) -> None:
    for item in files:
        remote = hub.download(EXPECTED_REPO, item.remote_path, commit_id)
        if hashlib.sha256(remote).hexdigest() != item.sha256:
            raise PublicationError(f"remote digest mismatch: {item.remote_path}")


def _receipt_payload(
    files: tuple[PublicationFile, ...], *, commit_id: str, inventory_digest: str
) -> dict[str, object]:
    return {
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
    }


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
    receipt, receipt_files = _load_receipt(receipt_path)
    resume_result = _resume_result(
        receipt,
        inventory_digest=inventory_digest,
        file_count=len(files),
    )
    if resume_result is not None:
        return resume_result
    commit = _plan_commit(files, _previous_file_digests(receipt_files))
    commit_id = hub.commit(commit)
    _verify_remote_files(hub, commit_id, commit.files)
    _write_receipt(
        receipt_path,
        _receipt_payload(files, commit_id=commit_id, inventory_digest=inventory_digest),
    )
    return PublicationResult("published", commit_id, len(files))
