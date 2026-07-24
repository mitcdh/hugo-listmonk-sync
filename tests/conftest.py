from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import httpx
import pytest

from hugo_listmonk_sync.config import Config
from hugo_listmonk_sync.http import RetryingHttpClient, RetryPolicy


@pytest.fixture
def base_env() -> dict[str, str]:
    return {
        "NEWSLETTER_JSON_URL": "https://hugo.example/newsletter.json",
        "LISTMONK_BASE_URL": "https://listmonk.example",
        "LISTMONK_API_USERNAME": "api-user",
        "LISTMONK_API_TOKEN": "secret-token",
        "LISTMONK_LIST_IDS": "4, 9",
    }


@pytest.fixture
def config(base_env: dict[str, str]) -> Config:
    return Config.from_env(base_env)


@pytest.fixture
def make_config(config: Config):
    def factory(**overrides: object) -> Config:
        return replace(config, **overrides)

    return factory


@pytest.fixture
def make_retrying_http() -> Iterator:
    clients: list[httpx.Client] = []

    def factory(
        *,
        auth: httpx.Auth | None = None,
        max_retries: int = 3,
        sleep=None,
    ) -> RetryingHttpClient:
        client = httpx.Client(auth=auth)
        clients.append(client)
        return RetryingHttpClient(
            client,
            RetryPolicy(max_retries=max_retries),
            sleep=sleep or (lambda _seconds: None),
        )

    yield factory
    for client in clients:
        client.close()
