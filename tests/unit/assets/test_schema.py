import pyarrow as pa
import pytest

from osm_polygon_image_tag.assets.schema import ASSET_STATUSES, asset_schema, validate_status

EXPECTED_ASSET_COLUMNS = [
    "source_pbf",
    "source_polygon_shard",
    "osm_type",
    "osm_id",
    "osm_version",
    "provider",
    "source_tag_key",
    "source_tag_value",
    "canonical_reference",
    "provider_asset_id",
    "asset_index",
    "relation_kind",
    "page_url",
    "image_url",
    "thumbnail_url",
    "image_url_expires_at",
    "mime_type",
    "width",
    "height",
    "license_id",
    "license_url",
    "author",
    "status",
    "reason",
    "category_truncated",
    "retry_after",
    "resolver_contract_version",
    "response_sha256",
]


def test_asset_schema_matches_the_public_contract() -> None:
    schema = asset_schema()

    assert schema.names == EXPECTED_ASSET_COLUMNS
    assert schema.field("osm_id").type == pa.int64()
    assert schema.field("osm_version").type == pa.int32()
    assert schema.field("osm_version").nullable is True
    assert schema.field("asset_index").nullable is False
    assert schema.field("image_url_expires_at").type == pa.timestamp("ms", tz="UTC")
    assert schema.field("retry_after").type == pa.timestamp("ms", tz="UTC")
    assert schema.metadata[b"osm_polygon_image_asset_schema_version"] == b"1"
    assert b"geo" not in schema.metadata


@pytest.mark.parametrize("status", sorted(ASSET_STATUSES))
def test_every_finite_status_is_valid(status: str) -> None:
    assert validate_status(status) == status


def test_unknown_status_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported asset status"):
        validate_status("invented")
