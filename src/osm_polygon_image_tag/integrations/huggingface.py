"""Hugging Face dataset integration.

This module owns every interaction with the Hugging Face Hub SDK so the rest of
the pipeline never imports a provider library directly. It exposes:

- ``PublicationFile``: a payload describing one file to upload.
- ``HubCommit``: an atomic upload/deletion transaction.
- ``Hub``: a structural protocol the publication planner depends on.
- ``HuggingFaceHub``: the concrete adapter that performs the commit and the
  verification download.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from huggingface_hub import (
    CommitOperationAdd,
    CommitOperationDelete,
    HfApi,
    hf_hub_download,
)

from osm_polygon_image_tag.core.errors import PublicationError


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
