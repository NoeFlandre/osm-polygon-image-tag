from collections.abc import Mapping
from datetime import datetime

from osm_polygon_image_tag.assets.references import SourceReference
from osm_polygon_image_tag.assets.resolution import ResolutionRecord


def _datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return None


def _base(
    polygon: Mapping[str, object],
    source_shard: str,
    reference: SourceReference,
    record: ResolutionRecord,
) -> dict[str, object]:
    return {
        "source_pbf": polygon["source_pbf"],
        "source_polygon_shard": source_shard,
        "osm_type": polygon["osm_type"],
        "osm_id": polygon["osm_id"],
        "osm_version": polygon.get("osm_version"),
        "provider": reference.provider,
        "source_tag_key": reference.source_tag_key,
        "source_tag_value": reference.source_tag_value,
        "canonical_reference": reference.canonical_reference,
        "relation_kind": (
            "category_membership"
            if reference.resolver_kind == "commons_category"
            else "direct_reference"
        ),
        "reason": record.reason,
        "category_truncated": record.category_truncated,
        "retry_after": record.retry_after,
        "resolver_contract_version": record.resolver_contract_version,
        "response_sha256": record.response_sha256,
    }


def asset_rows(
    polygon: Mapping[str, object],
    source_shard: str,
    reference: SourceReference,
    record: ResolutionRecord,
) -> list[dict[str, object]]:
    base = _base(polygon, source_shard, reference, record)
    rows: list[dict[str, object]] = []
    for index, asset in enumerate(record.assets):
        row = {
            **base,
            "provider_asset_id": asset.get("provider_asset_id"),
            "asset_index": index,
            "page_url": asset.get("page_url"),
            "image_url": asset.get("image_url"),
            "thumbnail_url": asset.get("thumbnail_url"),
            "image_url_expires_at": _datetime(asset.get("image_url_expires_at")),
            "mime_type": asset.get("mime_type"),
            "width": asset.get("width"),
            "height": asset.get("height"),
            "license_id": asset.get("license_id"),
            "license_url": asset.get("license_url"),
            "author": asset.get("author"),
            "status": "resolved_page_only" if record.status == "resolved_page_only" else "resolved",
        }
        rows.append(row)
    if not rows or record.category_truncated:
        rows.append(
            {
                **base,
                "provider_asset_id": None,
                "asset_index": len(rows),
                "page_url": None,
                "image_url": None,
                "thumbnail_url": None,
                "image_url_expires_at": None,
                "mime_type": None,
                "width": None,
                "height": None,
                "license_id": None,
                "license_url": None,
                "author": None,
                "status": record.status,
            }
        )
    return rows
