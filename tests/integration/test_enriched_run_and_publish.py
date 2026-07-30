import asyncio
from dataclasses import asdict
from pathlib import Path

import pyarrow.parquet as pq
import yaml
from shapely import to_wkb
from shapely.geometry import Polygon

from osm_polygon_image_tag.artifacts.publication import (
    EXPECTED_REPO,
    PublicationResult,
    publish_dataset,
)
from osm_polygon_image_tag.artifacts.publication_types import HubCommit
from osm_polygon_image_tag.artifacts.reporting import generate_metadata
from osm_polygon_image_tag.artifacts.storage import write_geoparquet
from osm_polygon_image_tag.assets.builder import build_asset_shard
from osm_polygon_image_tag.assets.cache import ResolutionCache, ResolutionRecord
from osm_polygon_image_tag.assets.references import SourceReference
from osm_polygon_image_tag.core.config import PipelinePaths
from osm_polygon_image_tag.core.manifest import (
    DATASET_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    PROCESSING_CONTRACT_VERSION,
    Manifest,
    OutputIdentity,
    RunCounts,
    SourceIdentity,
    file_sha256,
    write_manifest,
)
from osm_polygon_image_tag.ingest.extraction import ExportRecord
from osm_polygon_image_tag.ingest.transform import AcceptedRow, transform_record
from osm_polygon_image_tag.resolvers.types import ResolvedAsset
from osm_polygon_image_tag.runtime.enrichment import EnrichmentWorker
from osm_polygon_image_tag.runtime.orchestrator import StopToken, run_all


class _Hub:
    def __init__(self) -> None:
        self.commits: list[HubCommit] = []
        self.files: dict[str, bytes] = {}

    def commit(self, commit: HubCommit) -> str:
        self.commits.append(commit)
        for path in commit.deletions:
            self.files.pop(path, None)
        self.files.update({item.remote_path: item.local_path.read_bytes() for item in commit.files})
        return f"commit-{len(self.commits)}"

    def download(self, repo_id: str, remote_path: str, revision: str) -> bytes:
        del repo_id, revision
        return self.files[remote_path]


class _Registry:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.mapillary_credentialed = False

    def capability(self, provider: str) -> str:
        if provider == "mapillary":
            return "credentialed" if self.mapillary_credentialed else "anonymous"
        return "public"

    async def resolve_reference(
        self,
        reference: SourceReference,
        *,
        bbox: tuple[float, float, float, float],
        resolver_contract_version: int,
    ) -> ResolutionRecord:
        del bbox
        self.calls.append(reference.canonical_reference)
        credentialed = reference.provider != "mapillary" or self.mapillary_credentialed
        asset = ResolvedAsset(
            page_url=f"https://provider.test/{reference.canonical_reference}",
            image_url=reference.canonical_reference if credentialed else None,
        )
        return ResolutionRecord(
            reference.provider,
            reference.canonical_reference,
            resolver_contract_version,
            "resolved" if credentialed else "resolved_page_only",
            (asdict(asset),),
            None,
        )

    async def aclose(self) -> None:
        return None


def _polygon_shard(
    root: Path,
    index: int,
    *,
    tags: dict[str, str] | None = None,
) -> tuple[Manifest, Path]:
    relative = f"data/region-{index}.parquet"
    output = root / relative
    record = ExportRecord(
        geometry_ewkb_hex=to_wkb(
            Polygon([(4.0, 50.0), (4.1, 50.0), (4.1, 50.1), (4.0, 50.1)]),
            hex=True,
        ),
        osm_type="way",
        osm_id=index,
        version=1,
        changeset=1,
        timestamp=None,
        tags=tags or {"image": f"https://images.example.test/{index}.jpg"},
    )
    transformed = transform_record(record, source_pbf=f"region-{index}.osm.pbf")
    assert isinstance(transformed, AcceptedRow)
    write_geoparquet([transformed.values], output)
    manifest = Manifest(
        MANIFEST_SCHEMA_VERSION,
        PROCESSING_CONTRACT_VERSION,
        DATASET_SCHEMA_VERSION,
        SourceIdentity(f"region-{index}.osm.pbf", 1, 1, f"{index}" * 64),
        OutputIdentity(relative, output.stat().st_size, file_sha256(output), 1),
        "osmium fixture",
        RunCounts(1, {}),
    )
    write_manifest(manifest, root / "manifests" / f"region-{index}.manifest.json")
    return manifest, output


def _worker(root: Path, registry: _Registry) -> EnrichmentWorker:
    return EnrichmentWorker(
        root,
        cache_factory=ResolutionCache.open,
        registry_factory=lambda: registry,
        stop_requested=lambda: False,
        progress=lambda _event: None,
    )


def test_resume_backfills_only_missing_asset_and_republishes_once(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    data_root = tmp_path / "generated"
    shards = [_polygon_shard(data_root, index) for index in range(1, 4)]
    registry = _Registry()
    with ResolutionCache.open(data_root) as cache:
        for manifest, output in shards[:2]:
            asyncio.run(
                build_asset_shard(
                    manifest,
                    output,
                    data_root,
                    cache=cache,
                    registry=registry,
                    stop_requested=lambda: False,
                    progress=lambda _event: None,
                )
            )
    registry.calls.clear()
    paths = PipelinePaths.build(source_root=source, data_root=data_root)
    hub = _Hub()

    def publish(root: Path) -> PublicationResult:
        return publish_dataset(root, confirm_repo=EXPECTED_REPO, hub=hub)

    first = run_all(
        paths,
        stop_token=StopToken(),
        enrichment_worker=_worker(data_root, registry),
        metadata_builder=generate_metadata,
        publisher=publish,
    )
    calls_after_first = list(registry.calls)
    second = run_all(
        paths,
        stop_token=StopToken(),
        enrichment_worker=_worker(data_root, registry),
        metadata_builder=generate_metadata,
        publisher=publish,
    )

    assert first.processed == 0
    assert (first.enrichment.built, first.enrichment.skipped) == (1, 2)
    assert len(calls_after_first) == 1
    assert second.enrichment.built == 0
    assert second.enrichment.skipped == 3
    assert registry.calls == calls_after_first
    assert len(hub.commits) == 1
    frontmatter = yaml.safe_load(
        (data_root / "README.md").read_bytes().split(b"---", maxsplit=2)[1]
    )
    assert [config["config_name"] for config in frontmatter["configs"]] == [
        "polygons",
        "image_assets",
    ]


def test_credential_transition_rebuilds_and_publishes_without_pbf_work(
    tmp_path: Path,
) -> None:
    source = tmp_path / "raw"
    source.mkdir()
    data_root = tmp_path / "generated"
    _manifest, polygon_path = _polygon_shard(
        data_root,
        1,
        tags={"mapillary": "2627502594079174"},
    )
    polygon_bytes = polygon_path.read_bytes()
    registry = _Registry()
    paths = PipelinePaths.build(source_root=source, data_root=data_root)
    hub = _Hub()

    def publish(root: Path) -> PublicationResult:
        return publish_dataset(root, confirm_repo=EXPECTED_REPO, hub=hub)

    anonymous = run_all(
        paths,
        stop_token=StopToken(),
        enrichment_worker=_worker(data_root, registry),
        metadata_builder=generate_metadata,
        publisher=publish,
    )
    registry.mapillary_credentialed = True
    credentialed = run_all(
        paths,
        stop_token=StopToken(),
        enrichment_worker=_worker(data_root, registry),
        metadata_builder=generate_metadata,
        publisher=publish,
    )

    asset_path = data_root / "assets" / "region-1.assets.parquet"
    assert anonymous.processed == credentialed.processed == 0
    assert anonymous.enrichment.built == credentialed.enrichment.built == 1
    assert polygon_path.read_bytes() == polygon_bytes
    assert list(source.glob("*.pbf")) == []
    assert pq.read_table(asset_path).column("image_url").to_pylist() == ["2627502594079174"]
    assert len(hub.commits) == 2
