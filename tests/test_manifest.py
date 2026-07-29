from pathlib import Path

import pytest

from osm_polygon_image_tag.manifest import (
    Manifest,
    ManifestError,
    OutputIdentity,
    RunCounts,
    file_sha256,
    read_manifest,
    source_identity,
    write_manifest,
)


def _manifest(source: Path, output: Path) -> Manifest:
    return Manifest(
        manifest_schema_version=1,
        processing_contract_version=1,
        dataset_schema_version=1,
        source=source_identity(source, relative_path="region.osm.pbf"),
        output=OutputIdentity(
            relative_path="data/region.parquet",
            size_bytes=output.stat().st_size,
            sha256=file_sha256(output),
            row_count=3,
        ),
        osmium_version="osmium version 1.19.1",
        counts=RunCounts(accepted_rows=3, rejections={"invalid_geometry": 1}),
    )


def test_manifest_is_canonical_atomic_and_round_trips(tmp_path: Path) -> None:
    source = tmp_path / "region.osm.pbf"
    output = tmp_path / "region.parquet"
    source.write_bytes(b"source")
    output.write_bytes(b"output")
    manifest = _manifest(source, output)
    path = tmp_path / "manifests" / "region.json"

    write_manifest(manifest, path)

    assert read_manifest(path) == manifest
    first = path.read_bytes()
    write_manifest(manifest, path)
    assert path.read_bytes() == first
    assert first.endswith(b"\n")
    assert not list(path.parent.glob("*.tmp"))


def test_source_identity_detects_content_drift(tmp_path: Path) -> None:
    source = tmp_path / "region.osm.pbf"
    source.write_bytes(b"a")
    first = source_identity(source, relative_path="region.osm.pbf")
    source.write_bytes(b"b")

    assert source_identity(source, relative_path="region.osm.pbf") != first


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"{}",
        b'{"manifest_schema_version":99}\n',
    ],
)
def test_read_rejects_corrupt_or_incomplete_manifest(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "manifest.json"
    path.write_bytes(payload)

    with pytest.raises(ManifestError):
        read_manifest(path)


def test_file_sha256_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"abc")

    assert file_sha256(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
