# Assets

This package owns the independently versioned, one-to-many image-asset layer.
It reads finalized polygon shards, preserves exact source references, and
writes deterministic asset shards and manifests. It never performs PBF
extraction and never imports runtime, ingest, integration, or publication code.

The private `cache/resolutions.sqlite` uses WAL, full synchronous commits, and
canonical payload digests. Completed shard reuse depends on the polygon
identity and the shard-specific resolution snapshot, not on unrelated cache
writes.
