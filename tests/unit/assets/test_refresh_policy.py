from datetime import UTC, datetime, timedelta

import pytest

from osm_polygon_image_tag.assets.refresh_policy import (
    credential_refresh_required,
    retry_refresh_required,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _capability(provider: str) -> str:
    return "credentialed" if provider in {"mapillary", "flickr"} else "anonymous"


@pytest.mark.parametrize(
    ("provider", "status", "expected"),
    [
        ("wikimedia_commons", "requires_auth", True),
        ("mapillary", "requires_auth", True),
        ("flickr", "requires_auth", True),
        ("panoramax", "requires_auth", False),
        ("mapillary", "resolved_page_only", True),
        ("flickr", "resolved_page_only", True),
        ("panoramax", "resolved_page_only", False),
        ("image", "resolved", False),
    ],
)
def test_credential_refresh_policy_preserves_provider_rules(
    provider: str, status: str, expected: bool
) -> None:
    assert credential_refresh_required(provider, status, _capability) is expected


def test_credential_refresh_policy_does_not_probe_public_commons_capability() -> None:
    calls: list[str] = []

    def capability(provider: str) -> str:
        calls.append(provider)
        return "credentialed"

    assert credential_refresh_required("wikimedia_commons", "requires_auth", capability)
    assert calls == []


@pytest.mark.parametrize(
    ("status", "retry_after", "enabled", "expected"),
    [
        ("temporary_failure", None, True, True),
        ("temporary_failure", _NOW - timedelta(seconds=1), True, True),
        ("temporary_failure", _NOW + timedelta(hours=1), True, False),
        ("temporary_failure", _NOW, False, False),
        ("resolved", None, True, False),
    ],
)
def test_retry_refresh_policy_handles_due_and_disabled_retries(
    status: str, retry_after: datetime | None, enabled: bool, expected: bool
) -> None:
    assert retry_refresh_required(status, retry_after, _NOW, enabled=enabled) is expected


def test_retry_refresh_policy_treats_naive_timestamps_as_due() -> None:
    assert retry_refresh_required("temporary_failure", datetime(2026, 1, 1), _NOW)
