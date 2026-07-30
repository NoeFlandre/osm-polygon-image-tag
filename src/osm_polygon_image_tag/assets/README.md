# Assets

This package owns the independently versioned, one-to-many image-asset layer.
It reads finalized polygon shards, preserves exact source references, and
writes deterministic asset shards and manifests. It never performs PBF
extraction and never imports runtime, ingest, integration, or publication code.
