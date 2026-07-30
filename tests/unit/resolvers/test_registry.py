from osm_polygon_image_tag.resolvers.registry import ProviderLimit, ResolverRegistry


def test_registry_has_bounded_provider_limits_and_capabilities() -> None:
    registry = ResolverRegistry.build(environment={})

    assert registry.limit_for("wikimedia_commons") == ProviderLimit(4, 2.0)
    assert registry.limit_for("panoramax").max_concurrency <= 8
    assert registry.capability("mapillary") == "page_only_missing_token"
    assert registry.capability("flickr") == "page_only_missing_key"


def test_registry_reports_direct_capability_with_credentials() -> None:
    registry = ResolverRegistry.build(
        environment={"MAPILLARY_ACCESS_TOKEN": "x", "FLICKR_API_KEY": "y"}
    )

    assert registry.capability("mapillary") == "direct"
    assert registry.capability("flickr") == "direct"
