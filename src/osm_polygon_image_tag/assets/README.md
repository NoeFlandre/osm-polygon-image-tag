# Assets

This package owns the independently versioned, one-to-many image-asset layer.
It reads finalized polygon shards, preserves exact source references, and
writes deterministic asset shards and manifests. It never performs PBF
extraction and never imports runtime, ingest, integration, or publication code.

The private `cache/resolutions.sqlite` uses WAL, full synchronous commits, and
canonical payload digests. Completed shard reuse depends on the polygon
identity and the shard-specific resolution snapshot, not on unrelated cache
writes.

Source references whose canonical URL carries a secret-like query key
(`access_token`, `api_key`, `token`, `key`) are classified as non-cacheable by
`osm_polygon_image_tag.assets.resolution.is_cacheable_canonical_reference`.
They are resolved once with the original request URL but are never written to
the cache or included in a resolution snapshot, so they cannot abort a shard.
The strict `validate_resolution_record` guard still rejects any such reference
that reaches a durable cache write through any path. Resume reads only the
finalized polygon Parquet and cache; completed PBFs are never reopened.
