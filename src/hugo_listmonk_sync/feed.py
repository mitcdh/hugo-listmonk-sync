"""Hugo newsletter feed retrieval and validation."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from hugo_listmonk_sync.errors import FeedError
from hugo_listmonk_sync.http import RetryingHttpClient


@dataclass(frozen=True, slots=True)
class FeedPost:
    """One validated newsletter post."""

    name: str
    subject: str
    content: str
    attributes: dict[str, Any]


def validate_feed(
    payload: object,
    *,
    name_field: str,
    subject_field: str,
    content_field: str,
) -> tuple[FeedPost, ...]:
    """Fully validate a schema v1 feed and return normalized posts."""
    if not isinstance(payload, dict):
        raise FeedError("Feed root must be a JSON object")
    if payload.get("schemaVersion") != 1:
        raise FeedError("Feed schemaVersion must be exactly 1")
    raw_posts = payload.get("posts")
    if not isinstance(raw_posts, list):
        raise FeedError("Feed posts must be an array")

    posts: list[FeedPost] = []
    seen_names: set[str] = set()
    for index, raw_post in enumerate(raw_posts):
        if not isinstance(raw_post, dict):
            raise FeedError(f"Feed post at index {index} must be an object")
        name = _required_string(raw_post, name_field, index)
        subject = _required_string(raw_post, subject_field, index)
        content = _required_string(raw_post, content_field, index)
        if name in seen_names:
            raise FeedError(f"Duplicate {name_field!r} value {name!r} in feed")
        seen_names.add(name)
        posts.append(
            FeedPost(
                name=name,
                subject=subject,
                content=content,
                attributes=_post_attributes(raw_post),
            )
        )
    return tuple(posts)


class FeedClient:
    """Fetch and validate the configured Hugo feed."""

    def __init__(
        self,
        http: RetryingHttpClient,
        *,
        url: str,
        name_field: str,
        subject_field: str,
        content_field: str,
    ) -> None:
        self._http = http
        self._url = url
        self._name_field = name_field
        self._subject_field = subject_field
        self._content_field = content_field

    def fetch(self) -> tuple[FeedPost, ...]:
        """Fetch and fully validate all feed posts."""
        try:
            response = self._http.request(
                "GET",
                self._url,
                retry=True,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise FeedError(f"Could not fetch newsletter feed: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise FeedError("Newsletter feed response is not valid JSON") from exc
        return validate_feed(
            payload,
            name_field=self._name_field,
            subject_field=self._subject_field,
            content_field=self._content_field,
        )


def _required_string(
    post: Mapping[str, object],
    field: str,
    index: int,
) -> str:
    value = post.get(field)
    if not isinstance(value, str) or not value.strip():
        raise FeedError(
            f"Feed post at index {index} field {field!r} must be a non-empty string"
        )
    return value


def _post_attributes(post: Mapping[str, object]) -> dict[str, Any]:
    attributes = {
        key: copy.deepcopy(value)
        for key, value in post.items()
        if key not in {"html", "text"}
    }
    reading_time = attributes.get("readingTime")
    if isinstance(reading_time, (int, float)) and not isinstance(reading_time, bool):
        attributes["readingTime"] = f"{reading_time:g} min read"
    return attributes
