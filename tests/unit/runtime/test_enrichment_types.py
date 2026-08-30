import osm_polygon_image_tag.runtime.enrichment as enrichment
from osm_polygon_image_tag.runtime.enrichment_types import AssetJob, EnrichmentSummary


def test_enrichment_contracts_have_a_focused_owner() -> None:
    assert AssetJob.__module__ == "osm_polygon_image_tag.runtime.enrichment_types"
    assert EnrichmentSummary.__module__ == "osm_polygon_image_tag.runtime.enrichment_types"
    assert enrichment.AssetJob is AssetJob
    assert enrichment.EnrichmentSummary is EnrichmentSummary
