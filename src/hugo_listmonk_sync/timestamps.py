"""ISO 8601 timestamp parsing shared by feed validation and reconciliation."""

from __future__ import annotations

from datetime import datetime


def parse_aware_iso8601(value: str) -> datetime:
    """Parse an ISO 8601 timestamp and require an explicit UTC offset."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp is not valid ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone offset")
    return parsed
