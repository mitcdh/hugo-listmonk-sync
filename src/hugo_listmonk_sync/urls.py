"""URL handling shared by generated e-mail bodies."""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit


def resolve_content_url(value: str, base_url: str | None) -> str:
    """Resolve an article-relative URL when an absolute HTTP(S) base exists."""
    if base_url is None or value.startswith("#") or "{{" in value:
        return value
    parsed_base = urlsplit(base_url)
    if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
        return value
    return urljoin(base_url, value)
