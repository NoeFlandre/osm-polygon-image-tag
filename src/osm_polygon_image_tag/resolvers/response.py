from collections.abc import Mapping
from typing import Protocol, cast


class MetadataClient(Protocol):
    async def get_json(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> Mapping[str, object]: ...


def as_mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def as_sequence(value: object) -> list[object] | tuple[object, ...]:
    return cast(list[object] | tuple[object, ...], value) if isinstance(value, list | tuple) else ()


def as_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def as_integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
