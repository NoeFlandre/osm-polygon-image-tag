"""Schemas and validators for the public image and relationship tables."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

PUBLIC_IMAGE_SCHEMA_VERSION = 1
PUBLIC_LINK_SCHEMA_VERSION = 1


def public_image_schema() -> pa.Schema:
    """Return the one-row-per-image public schema."""
    utc_timestamp = pa.timestamp("ms", tz="UTC")
    fields = [
        pa.field("image_id", pa.string(), nullable=False),
        pa.field("provider", pa.string(), nullable=False),
        pa.field("canonical_reference", pa.string(), nullable=False),
        pa.field("provider_asset_id", pa.string()),
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
        pa.field("source_pbfs", pa.list_(pa.field("element", pa.string())), nullable=False),
    ]
    return pa.schema(
        fields,
        metadata={
            b"osm_polygon_image_tag_public_image_schema_version": str(
                PUBLIC_IMAGE_SCHEMA_VERSION
            ).encode()
        },
    )


def public_link_schema() -> pa.Schema:
    """Return the many-to-many polygon/image relationship schema."""
    fields = [
        pa.field("osm_type", pa.string(), nullable=False),
        pa.field("osm_id", pa.int64(), nullable=False),
        pa.field("osm_version", pa.int32()),
        pa.field("image_id", pa.string(), nullable=False),
        pa.field("provider", pa.string(), nullable=False),
        pa.field("source_tag_key", pa.string(), nullable=False),
        pa.field("source_tag_value", pa.string(), nullable=False),
        pa.field("canonical_reference", pa.string(), nullable=False),
        pa.field("asset_index", pa.int32(), nullable=False),
        pa.field("relation_kind", pa.string(), nullable=False),
        pa.field("source_pbfs", pa.list_(pa.field("element", pa.string())), nullable=False),
        pa.field(
            "observed_osm_versions",
            pa.list_(pa.field("element", pa.int32())),
            nullable=False,
        ),
    ]
    return pa.schema(
        fields,
        metadata={
            b"osm_polygon_image_tag_public_link_schema_version": str(
                PUBLIC_LINK_SCHEMA_VERSION
            ).encode()
        },
    )


def validate_public_image_parquet(path: Path, *, expected_rows: int | None = None) -> None:
    try:
        parquet = pq.ParquetFile(path)
    except (OSError, pa.ArrowException) as error:
        raise ValueError("public image Parquet is invalid") from error
    if not parquet.schema_arrow.equals(public_image_schema(), check_metadata=True):
        raise ValueError("public image Parquet schema does not match")
    if expected_rows is not None and parquet.metadata.num_rows != expected_rows:
        raise ValueError("public image row count does not match")


def validate_public_link_parquet(path: Path, *, expected_rows: int | None = None) -> None:
    try:
        parquet = pq.ParquetFile(path)
    except (OSError, pa.ArrowException) as error:
        raise ValueError("public link Parquet is invalid") from error
    if not parquet.schema_arrow.equals(public_link_schema(), check_metadata=True):
        raise ValueError("public link Parquet schema does not match")
    if expected_rows is not None and parquet.metadata.num_rows != expected_rows:
        raise ValueError("public link row count does not match")


__all__ = [
    "PUBLIC_IMAGE_SCHEMA_VERSION",
    "PUBLIC_LINK_SCHEMA_VERSION",
    "public_image_schema",
    "public_link_schema",
    "validate_public_image_parquet",
    "validate_public_link_parquet",
]
