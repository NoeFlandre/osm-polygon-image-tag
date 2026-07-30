"""Provider-neutral publication payloads and outbound Hub port."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


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
