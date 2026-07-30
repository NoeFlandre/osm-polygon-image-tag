import shutil
import subprocess
from pathlib import Path

import pytest

from osm_polygon_image_tag.artifacts.publication import (
    EXPECTED_REPO,
    PublicationResult,
    publish_dataset,
)
from osm_polygon_image_tag.core.config import PipelinePaths
from osm_polygon_image_tag.integrations.huggingface import HubCommit
from osm_polygon_image_tag.runtime.orchestrator import run_all

FIXTURE = Path("tests/fixtures/image_tag_coverage.osm")


class MemoryHub:
    def __init__(self) -> None:
        self.commits: list[HubCommit] = []
        self.remote: dict[str, bytes] = {}

    def commit(self, commit: HubCommit) -> str:
        self.commits.append(commit)
        for path in commit.deletions:
            self.remote.pop(path, None)
        self.remote.update(
            {item.remote_path: item.local_path.read_bytes() for item in commit.files}
        )
        return f"commit-{len(self.commits)}"

    def download(self, _repo_id: str, remote_path: str, _revision: str) -> bytes:
        return self.remote[remote_path]


@pytest.mark.integration
def test_real_pipeline_build_publish_verify_and_resume(tmp_path: Path) -> None:
    osmium = shutil.which("osmium")
    assert osmium is not None
    source = tmp_path / "raw"
    source.mkdir()
    subprocess.run(  # noqa: S603 - controlled local fixture argv.
        [osmium, "cat", str(FIXTURE), "-o", str(source / "coverage.osm.pbf")],
        check=True,
        capture_output=True,
        timeout=30,
    )
    paths = PipelinePaths.build(source_root=source, data_root=tmp_path / "generated")
    hub = MemoryHub()

    def publish(root: Path) -> PublicationResult:
        return publish_dataset(root, confirm_repo=EXPECTED_REPO, hub=hub)

    first = run_all(paths, publisher=publish)
    second = run_all(paths, publisher=publish)

    assert first.built == 1
    assert first.accepted_rows == 9
    assert second.skipped == 1
    assert len(hub.commits) == 1
    assert (paths.data_root / "receipts/publication.json").is_file()
