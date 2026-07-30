"""Runtime: pipeline assembly, orchestration, preflight, and package resources.

Modules in this package compose the ingest, artifacts, and integrations layers
into resumable workflows and expose the operator-facing preflight checks. They
may depend on every other subpackage but never on the CLI entry point.
"""
