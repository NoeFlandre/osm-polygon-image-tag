import pytest

from osm_polygon_image_tag.assets.resolution import (
    is_cacheable_canonical_reference,
)

PANORAMAX_ID = "4492cea4-1018-4285-8074-cf3d37f3c673"


@pytest.mark.parametrize("secret", ["access_token", "api_key", "token", "key"])
def test_secret_query_keys_make_reference_non_cacheable(secret: str) -> None:
    assert not is_cacheable_canonical_reference(f"https://provider.test/item?{secret}=value")


@pytest.mark.parametrize("secret", ["ACCESS_TOKEN", "API_Key", "TOKEN", "Key"])
def test_uppercase_secret_query_keys_make_reference_non_cacheable(secret: str) -> None:
    assert not is_cacheable_canonical_reference(f"https://provider.test/item?{secret}=value")


@pytest.mark.parametrize(
    "encoded_key",
    [
        "%6Bey",  # decodes to "key"
        "%61ccess_token",  # decodes to "access_token"
    ],
)
def test_percent_encoded_secret_query_key_is_non_cacheable(encoded_key: str) -> None:
    assert not is_cacheable_canonical_reference(f"https://provider.test/item?{encoded_key}=value")


def test_secret_value_mixed_with_other_query_stays_non_cacheable() -> None:
    assert not is_cacheable_canonical_reference(
        "https://photos.google.com/share/abc/photo/xyz?sz=w1600&key=value&hl=en"
    )


@pytest.mark.parametrize(
    "reference",
    [
        PANORAMAX_ID,
        "File:Example.jpg",
        "https://example.test/photo.jpg",
        "https://panoramax.test/api/photos?sequence=abc&foo=bar",
        "6831725321",
    ],
)
def test_ordinary_references_remain_cacheable(reference: str) -> None:
    assert is_cacheable_canonical_reference(reference)


def test_reference_without_query_is_cacheable() -> None:
    assert is_cacheable_canonical_reference("https://example.test/path")
    assert is_cacheable_canonical_reference("")
