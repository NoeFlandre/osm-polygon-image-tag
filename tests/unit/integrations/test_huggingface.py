from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from osm_polygon_image_tag.artifacts.publication import EXPECTED_REPO
from osm_polygon_image_tag.artifacts.publication_types import HubCommit, PublicationFile
from osm_polygon_image_tag.core.errors import PublicationError
from osm_polygon_image_tag.core.manifest import file_sha256
from osm_polygon_image_tag.integrations.huggingface import HuggingFaceHub


def test_real_hub_adapter_uses_dataset_commit_and_pinned_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "README.md"
    source.write_bytes(b"content")
    calls: dict[str, Any] = {}

    class Api:
        def create_commit(self, **kwargs: object) -> object:
            calls["commit"] = kwargs
            return SimpleNamespace(oid="abc123")

    def download(**kwargs: object) -> str:
        calls["download"] = kwargs
        remote = tmp_path / "remote"
        remote.write_bytes(b"content")
        return str(remote)

    monkeypatch.setattr("osm_polygon_image_tag.integrations.huggingface.HfApi", Api)
    monkeypatch.setattr("osm_polygon_image_tag.integrations.huggingface.hf_hub_download", download)
    hub = HuggingFaceHub()
    commit_id = hub.commit(
        HubCommit(
            EXPECTED_REPO,
            "dataset",
            "message",
            (PublicationFile(source, "README.md", file_sha256(source), 7),),
            ("data/stale.parquet",),
        )
    )
    content = hub.download(EXPECTED_REPO, "README.md", commit_id)

    assert commit_id == "abc123"
    assert content == b"content"
    assert calls["commit"]["repo_type"] == "dataset"
    assert calls["download"]["revision"] == "abc123"
    assert calls["download"]["repo_type"] == "dataset"


def test_real_hub_adapter_wraps_client_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    class Api:
        def create_commit(self, **_kwargs: object) -> object:
            raise RuntimeError("secret client detail")

    monkeypatch.setattr("osm_polygon_image_tag.integrations.huggingface.HfApi", Api)
    hub = HuggingFaceHub()

    with pytest.raises(PublicationError, match="commit failed"):
        hub.commit(HubCommit(EXPECTED_REPO, "dataset", "message", ()))

    def fail_download(**_kwargs: object) -> str:
        raise RuntimeError("secret client detail")

    monkeypatch.setattr(
        "osm_polygon_image_tag.integrations.huggingface.hf_hub_download",
        fail_download,
    )
    with pytest.raises(PublicationError, match="verification failed"):
        hub.download(EXPECTED_REPO, "README.md", "abc")
