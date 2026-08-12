import ast
from pathlib import Path

from osm_polygon_image_tag.artifacts.catalog import PROVIDERS
from osm_polygon_image_tag.assets.polygon_input import (
    POLYGON_COLUMNS,
)
from osm_polygon_image_tag.assets.polygon_input import (
    REFERENCE_COLUMNS as INPUT_REFERENCE_COLUMNS,
)
from osm_polygon_image_tag.core.contracts import (
    IMAGE_REFERENCE_KEYS,
    PANORAMAX_VALUES_COLUMN,
    REFERENCE_COLUMNS,
    SCALAR_REFERENCE_COLUMNS,
)
from osm_polygon_image_tag.ingest.tag_policy import TARGET_TAG_KEYS


def test_image_reference_contract_is_shared_by_all_consumers() -> None:
    assert IMAGE_REFERENCE_KEYS == (
        "image",
        "wikimedia_commons",
        "mapillary",
        "panoramax",
        "kartaview",
        "flickr",
        "bubbleid",
    )
    assert TARGET_TAG_KEYS is IMAGE_REFERENCE_KEYS
    assert PROVIDERS is IMAGE_REFERENCE_KEYS
    assert INPUT_REFERENCE_COLUMNS is REFERENCE_COLUMNS
    assert POLYGON_COLUMNS[-len(REFERENCE_COLUMNS) :] == REFERENCE_COLUMNS
    assert SCALAR_REFERENCE_COLUMNS == (
        "image",
        "wikimedia_commons",
        "mapillary",
        "kartaview",
        "flickr",
        "bubbleid",
    )


def test_runtime_consumers_use_the_canonical_panoramax_column() -> None:
    consumers = (
        "src/osm_polygon_image_tag/assets/polygon_input.py",
        "src/osm_polygon_image_tag/assets/references.py",
        "src/osm_polygon_image_tag/artifacts/catalog.py",
        "src/osm_polygon_image_tag/artifacts/storage.py",
        "src/osm_polygon_image_tag/ingest/transform.py",
    )
    assert PANORAMAX_VALUES_COLUMN == "panoramax_values"
    for consumer in consumers:
        tree = ast.parse(Path(consumer).read_text(encoding="utf-8"))
        imports_constant = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "osm_polygon_image_tag.core.contracts"
            and any(alias.name == "PANORAMAX_VALUES_COLUMN" for alias in node.names)
            for node in ast.walk(tree)
        )
        assert imports_constant, consumer
        assert not any(
            isinstance(node, ast.Constant) and node.value == "panoramax_values"
            for node in ast.walk(tree)
        ), consumer


def test_references_uses_the_shared_keys_without_a_private_alias() -> None:
    tree = ast.parse(
        Path("src/osm_polygon_image_tag/assets/references.py").read_text(encoding="utf-8")
    )

    assert any(
        isinstance(node, ast.For)
        and isinstance(node.iter, ast.Name)
        and node.iter.id == "IMAGE_REFERENCE_KEYS"
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Name) and node.id == "_TARGET_KEYS" for node in ast.walk(tree)
    )
