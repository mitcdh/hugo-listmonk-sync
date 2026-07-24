"""HTTP retry support shared by feed and Listmonk clients."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TRANSIENT_STATUSES = frozenset({429, *range(500, 600)})


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Settings for bounded HTTP retries."""

    max_retries: int
    maximum_delay_seconds: float = 60.0


class RetryingHttpClient:
    """Small wrapper that retries only explicitly safe requests."""

    def __init__(
        self,
        client: httpx.Client,
        policy: RetryPolicy,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._policy = policy
        self._sleep = sleep

    def request(
        self,
        method: str,
        url: str,
        *,
        retry: bool,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make an HTTP request, optionally retrying transient failures."""
        attempt = 0
        while True:
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TransportError:
                if not retry or attempt >= self._policy.max_retries:
                    raise
                delay = self._backoff_delay(attempt, None)
                logger.warning(
                    "Transient network failure on %s %s; retrying in %.1fs",
                    method,
                    url,
                    delay,
                )
                self._sleep(delay)
                attempt += 1
                continue

            if (
                retry
                and response.status_code in _TRANSIENT_STATUSES
                and attempt < self._policy.max_retries
            ):
                delay = self._backoff_delay(
                    attempt,
                    response.headers.get("Retry-After"),
                )
                logger.warning(
                    "Transient HTTP %d on %s %s; retrying in %.1fs",
                    response.status_code,
                    method,
                    url,
                    delay,
                )
                response.close()
                self._sleep(delay)
                attempt += 1
                continue
            return response

    def _backoff_delay(self, attempt: int, retry_after: str | None) -> float:
        parsed = _parse_retry_after(retry_after)
        if parsed is not None:
            return min(parsed, self._policy.maximum_delay_seconds)
        return min(float(2**attempt), self._policy.maximum_delay_seconds)


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    stripped = value.strip()
    try:
        seconds = float(stripped)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(stripped)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
    return max(0.0, seconds)
