"""Ingest: read-only PBF discovery and area-feature extraction.

Modules in this package consume the immutable PBF source tree and produce
typed records describing OSM area features. They may depend on ``core`` and
on the local ``_data`` package-data resources, but never on runtime,
artifacts, or integrations.
"""
