import pyarrow as pa

ASSET_SCHEMA_VERSION = 1
RESOLVER_CONTRACT_VERSION = 1
ASSET_STATUSES = frozenset(
    {
        "resolved",
        "resolved_page_only",
        "not_direct_image",
        "category_empty",
        "category_truncated",
        "not_found",
        "private",
        "requires_auth",
        "invalid_reference",
        "unsupported",
        "temporary_failure",
    }
)


def asset_schema() -> pa.Schema:
    utc_timestamp = pa.timestamp("ms", tz="UTC")
    fields = [
        pa.field("source_pbf", pa.string(), nullable=False),
        pa.field("source_polygon_shard", pa.string(), nullable=False),
        pa.field("osm_type", pa.string(), nullable=False),
        pa.field("osm_id", pa.int64(), nullable=False),
        pa.field("osm_version", pa.int32()),
        pa.field("provider", pa.string(), nullable=False),
        pa.field("source_tag_key", pa.string(), nullable=False),
        pa.field("source_tag_value", pa.string(), nullable=False),
        pa.field("canonical_reference", pa.string(), nullable=False),
        pa.field("provider_asset_id", pa.string()),
        pa.field("asset_index", pa.int32(), nullable=False),
        pa.field("relation_kind", pa.string(), nullable=False),
        pa.field("page_url", pa.string()),
        pa.field("image_url", pa.string()),
        pa.field("thumbnail_url", pa.string()),
        pa.field("image_url_expires_at", utc_timestamp),
        pa.field("mime_type", pa.string()),
        pa.field("width", pa.int32()),
        pa.field("height", pa.int32()),
        pa.field("license_id", pa.string()),
        pa.field("license_url", pa.string()),
        pa.field("author", pa.string()),
        pa.field("status", pa.string(), nullable=False),
        pa.field("reason", pa.string()),
        pa.field("category_truncated", pa.bool_(), nullable=False),
        pa.field("retry_after", utc_timestamp),
        pa.field("resolver_contract_version", pa.int32(), nullable=False),
        pa.field("response_sha256", pa.string()),
    ]
    return pa.schema(
        fields,
        metadata={b"osm_polygon_image_asset_schema_version": str(ASSET_SCHEMA_VERSION).encode()},
    )


def validate_status(value: str) -> str:
    if value not in ASSET_STATUSES:
        raise ValueError(f"unsupported asset status: {value}")
    return value
