import osm_polygon_image_tag.runtime.pipeline as pipeline
from osm_polygon_image_tag.runtime.pipeline_build import build_source_output


def test_source_building_is_owned_by_focused_module() -> None:
    assert build_source_output.__module__ == ("osm_polygon_image_tag.runtime.pipeline_build")
    assert pipeline._build_source_output is build_source_output
