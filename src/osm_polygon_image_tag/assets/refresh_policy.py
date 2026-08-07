"""Credential and retry decisions shared by asset resume paths."""

from collections.abc import Callable
from datetime import datetime

Capability = Callable[[str], str]


def credential_refresh_required(
    provider: str,
    status: object,
    capability: Capability,
) -> bool:
    """Return whether credentials can improve a cached provider result."""
    if status == "requires_auth":
        return provider == "wikimedia_commons" or capability(provider) == "credentialed"
    return (
        status == "resolved_page_only"
        and provider in {"mapillary", "flickr"}
        and capability(provider) == "credentialed"
    )


def retry_refresh_required(
    status: object,
    retry_after: object,
    now: datetime,
    *,
    enabled: bool = True,
) -> bool:
    """Return whether a temporary failure is due for another attempt."""
    return (
        enabled
        and status == "temporary_failure"
        and (
            not isinstance(retry_after, datetime)
            or retry_after.tzinfo is None
            or retry_after <= now
        )
    )
