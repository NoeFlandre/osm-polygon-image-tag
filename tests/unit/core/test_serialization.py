from datetime import datetime

from osm_polygon_image_tag.core.serialization import canonical_json_bytes


def test_canonical_json_bytes_preserves_deterministic_payload() -> None:
    payload = {"bytes": b"\x01", "when": datetime(2024, 1, 2), "items": [b"\x02"]}
    expected = (
        b'{"bytes":{"__bytes__":"01"},"items":[{"__bytes__":"02"}],"when":"2024-01-02T00:00:00"}'
    )

    assert canonical_json_bytes(payload) == expected
    assert canonical_json_bytes(payload, newline=True) == expected + b"\n"
