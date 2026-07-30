# `osm_polygon_image_tag/`

The production package exposes the command-line pipeline and package version.
Responsibility-specific code lives in `core`, `ingest`, `artifacts`, `runtime`,
and `integrations`; `_data` contains read-only packaged resources.

The supported public interface is the installed `osm-polygon-image-tag` CLI
and `osm_polygon_image_tag.__version__`. Internal modules may evolve without a
compatibility guarantee.
