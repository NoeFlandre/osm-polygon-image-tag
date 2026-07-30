"""Artifacts: local persistence, catalog, reporting, and publication planning.

Modules in this package own every artifact that lives inside the managed data
root: GeoParquet shards, manifests, the rebuildable catalog, the generated
dataset card, and the publication inventory and receipts. They may depend on
``core`` and ``integrations`` (for the remote commit payloads) but never on the
CLI or runtime orchestration.
"""
