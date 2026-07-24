from __future__ import annotations

import httpx
import pytest
import respx

from hugo_listmonk_sync.http import RetryingHttpClient, RetryPolicy


@respx.mock
def test_retries_network_errors_and_transient_responses():
    route = respx.get("https://example.test/resource").mock(
        side_effect=[
            httpx.ConnectError("offline"),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    delays = []
    with httpx.Client() as client:
        http = RetryingHttpClient(
            client,
            RetryPolicy(max_retries=3),
            sleep=delays.append,
        )
        response = http.request(
            "GET",
            "https://example.test/resource",
            retry=True,
        )

    assert response.status_code == 200
    assert route.call_count == 3
    assert delays == [1, 2]


@respx.mock
def test_honors_numeric_retry_after():
    respx.get("https://example.test/resource").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "17"}),
            httpx.Response(200),
        ]
    )
    delays = []
    with httpx.Client() as client:
        http = RetryingHttpClient(
            client,
            RetryPolicy(max_retries=1),
            sleep=delays.append,
        )
        http.request("GET", "https://example.test/resource", retry=True)

    assert delays == [17]


@respx.mock
def test_bounds_retry_after_delay():
    respx.get("https://example.test/resource").mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "600"}),
            httpx.Response(200),
        ]
    )
    delays = []
    with httpx.Client() as client:
        http = RetryingHttpClient(
            client,
            RetryPolicy(max_retries=1, maximum_delay_seconds=12),
            sleep=delays.append,
        )
        http.request("GET", "https://example.test/resource", retry=True)

    assert delays == [12]


@respx.mock
def test_does_not_retry_when_disabled():
    route = respx.post("https://example.test/resource").mock(
        side_effect=httpx.ConnectError("uncertain result")
    )
    with httpx.Client() as client:
        http = RetryingHttpClient(
            client,
            RetryPolicy(max_retries=3),
            sleep=lambda _: pytest.fail("sleep should not be called"),
        )
        with pytest.raises(httpx.ConnectError):
            http.request("POST", "https://example.test/resource", retry=False)

    assert route.call_count == 1


@respx.mock
def test_returns_final_transient_response_after_retry_limit():
    route = respx.get("https://example.test/resource").mock(
        return_value=httpx.Response(503)
    )
    with httpx.Client() as client:
        http = RetryingHttpClient(
            client,
            RetryPolicy(max_retries=2),
            sleep=lambda _: None,
        )
        response = http.request(
            "GET",
            "https://example.test/resource",
            retry=True,
        )

    assert response.status_code == 503
    assert route.call_count == 3


@respx.mock
def test_does_not_retry_non_transient_errors():
    route = respx.get("https://example.test/resource").mock(
        return_value=httpx.Response(401)
    )
    with httpx.Client() as client:
        http = RetryingHttpClient(
            client,
            RetryPolicy(max_retries=3),
            sleep=lambda _: pytest.fail("sleep should not be called"),
        )
        response = http.request(
            "GET",
            "https://example.test/resource",
            retry=True,
        )

    assert response.status_code == 401
    assert route.call_count == 1
