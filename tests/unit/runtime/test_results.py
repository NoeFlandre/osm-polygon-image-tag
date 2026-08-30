import osm_polygon_image_tag.runtime.orchestrator as orchestrator
from osm_polygon_image_tag.runtime.results import RunSummary, VerifySummary


def test_runtime_result_contracts_have_a_focused_owner() -> None:
    assert RunSummary.__module__ == "osm_polygon_image_tag.runtime.results"
    assert VerifySummary.__module__ == "osm_polygon_image_tag.runtime.results"
    assert orchestrator.RunSummary is RunSummary
    assert orchestrator.VerifySummary is VerifySummary
