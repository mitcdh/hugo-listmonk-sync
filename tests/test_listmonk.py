from __future__ import annotations

import httpx
import pytest
import respx

from hugo_listmonk_sync.errors import ListmonkError
from hugo_listmonk_sync.feed import FeedPost
from hugo_listmonk_sync.listmonk import ListmonkClient


def post() -> FeedPost:
    return FeedPost(
        name="post-key",
        subject="Post title",
        content="<p>Post body</p>",
        attributes={
            "key": "post-key",
            "title": "Post title",
            "image": "",
            "readingTime": "5 min read",
        },
    )


def client(config, make_retrying_http, *, max_retries=3, sleep=None):
    auth = httpx.BasicAuth(
        config.listmonk_api_username,
        config.listmonk_api_token,
    )
    return ListmonkClient(
        make_retrying_http(
            auth=auth,
            max_retries=max_retries,
            sleep=sleep,
        ),
        config,
    )


def test_creation_payload_uses_configured_defaults_and_omits_unset(
    config, make_retrying_http
):
    listmonk = client(config, make_retrying_http)

    payload = listmonk.creation_payload(post())

    assert payload == {
        "name": "post-key",
        "subject": "Post title",
        "lists": [4, 9],
        "type": "regular",
        "content_type": "html",
        "body": "<p>Post body</p>",
        "messenger": "email",
        "attribs": {
            "post": {
                "key": "post-key",
                "title": "Post title",
                "image": "",
                "readingTime": "5 min read",
            }
        },
    }
    assert "template_id" not in payload
    assert "from_email" not in payload
    assert "tags" not in payload


def test_creation_payload_includes_configured_optional_fields(
    make_config,
    make_retrying_http,
):
    config = make_config(
        listmonk_template_id=7,
        listmonk_from_email="News <news@example.test>",
        listmonk_campaign_tags=("hugo", "news"),
    )
    listmonk = client(config, make_retrying_http)

    payload = listmonk.creation_payload(post())

    assert payload["template_id"] == 7
    assert payload["from_email"] == "News <news@example.test>"
    assert payload["tags"] == ["hugo", "news"]


def test_update_payload_preserves_all_non_feed_settings_and_other_attribs():
    existing = {
        "id": 41,
        "name": "post-key",
        "status": "draft",
        "subject": "Old title",
        "body": "Old body",
        "lists": [{"id": 10, "name": "One"}, {"id": 20, "name": "Two"}],
        "from_email": "Existing <existing@example.test>",
        "type": "optin",
        "content_type": "markdown",
        "body_source": {"blocks": ["unchanged"]},
        "altbody": "Existing alternate body",
        "send_at": "2026-08-01T00:00:00Z",
        "messenger": "custom",
        "template_id": 13,
        "tags": ["existing"],
        "headers": [{"X-Custom": "kept"}],
        "attribs": {
            "audience": "researchers",
            "post": {"key": "old", "stale": True},
        },
        "created_at": "read-only and not sent",
    }

    payload = ListmonkClient.update_payload(existing, post())

    assert payload == {
        "name": "post-key",
        "subject": "Post title",
        "body": "<p>Post body</p>",
        "lists": [10, 20],
        "from_email": "Existing <existing@example.test>",
        "type": "optin",
        "content_type": "markdown",
        "body_source": {"blocks": ["unchanged"]},
        "altbody": "Existing alternate body",
        "send_at": "2026-08-01T00:00:00Z",
        "messenger": "custom",
        "template_id": 13,
        "tags": ["existing"],
        "headers": [{"X-Custom": "kept"}],
        "attribs": {
            "audience": "researchers",
            "post": {
                "key": "post-key",
                "title": "Post title",
                "image": "",
                "readingTime": "5 min read",
            },
        },
    }
    assert existing["attribs"]["post"] == {"key": "old", "stale": True}
    assert "created_at" not in payload


def test_update_payload_accepts_list_ids_already_in_request_shape():
    existing = {
        "id": 1,
        "status": "draft",
        "lists": [4, 9],
        "attribs": {},
    }

    payload = ListmonkClient.update_payload(existing, post())

    assert payload["lists"] == [4, 9]


@pytest.mark.parametrize(
    "change",
    [
        {"status": "sent"},
        {"lists": None},
        {"lists": [{"name": "missing id"}]},
        {"attribs": []},
    ],
)
def test_update_payload_rejects_unsafe_or_malformed_campaign(change):
    existing = {
        "id": 1,
        "status": "draft",
        "lists": [{"id": 4}],
        "attribs": {},
    }
    existing.update(change)

    with pytest.raises(ListmonkError):
        ListmonkClient.update_payload(existing, post())


@respx.mock
def test_list_campaigns_uses_all_no_body_and_basic_auth(
    config,
    make_retrying_http,
):
    route = respx.get(
        "https://listmonk.example/api/campaigns",
        params={"per_page": "all", "no_body": "true"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "results": [
                        {"id": 1, "name": "Exact", "status": "draft"},
                        {"id": 2, "name": "exact", "status": "finished"},
                    ]
                }
            },
        )
    )
    listmonk = client(config, make_retrying_http)

    campaigns = listmonk.list_campaigns()

    assert [(item.id, item.name, item.status) for item in campaigns] == [
        (1, "Exact", "draft"),
        (2, "exact", "finished"),
    ]
    assert route.called
    assert route.calls[0].request.headers["Authorization"].startswith("Basic ")


@respx.mock
def test_create_posts_correct_payload_once(config, make_retrying_http):
    route = respx.post("https://listmonk.example/api/campaigns").mock(
        return_value=httpx.Response(200, json={"data": {"id": 51}})
    )
    listmonk = client(config, make_retrying_http)

    result = listmonk.create_campaign(post())

    assert result == {"id": 51}
    assert route.call_count == 1
    assert route.calls[0].request.method == "POST"
    assert route.calls[0].request.read()
    assert route.calls[0].request.headers["Content-Type"] == "application/json"
    assert route.calls[0].request.content


@respx.mock
def test_get_and_put_retry_transient_failures(
    config,
    make_retrying_http,
):
    get_route = respx.get("https://listmonk.example/api/campaigns/3").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(
                200,
                json={
                    "data": {
                        "id": 3,
                        "name": "post-key",
                        "status": "draft",
                        "lists": [{"id": 4}],
                        "attribs": {},
                    }
                },
            ),
        ]
    )
    put_route = respx.put("https://listmonk.example/api/campaigns/3").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"data": {"id": 3}}),
        ]
    )
    listmonk = client(config, make_retrying_http)

    existing = listmonk.get_campaign(3)
    result = listmonk.update_campaign(3, existing, post())

    assert result == {"id": 3}
    assert get_route.call_count == 2
    assert put_route.call_count == 2


@respx.mock
def test_post_is_never_retried_after_transient_response(
    config,
    make_retrying_http,
):
    route = respx.post("https://listmonk.example/api/campaigns").mock(
        return_value=httpx.Response(503, json={"message": "try later"})
    )
    listmonk = client(config, make_retrying_http)

    with pytest.raises(ListmonkError, match="HTTP 503"):
        listmonk.create_campaign(post())

    assert route.call_count == 1


@respx.mock
def test_authentication_error_is_reported_without_retry(
    config,
    make_retrying_http,
):
    route = respx.get(
        "https://listmonk.example/api/campaigns",
        params={"per_page": "all", "no_body": "true"},
    ).mock(return_value=httpx.Response(401, json={"message": "unauthorized"}))
    listmonk = client(config, make_retrying_http)

    with pytest.raises(ListmonkError, match="401.*unauthorized"):
        listmonk.list_campaigns()

    assert route.call_count == 1


@pytest.mark.parametrize(
    "response",
    [
        {"data": []},
        {"data": {}},
        {"data": {"results": [None]}},
        {"data": {"results": [{"id": True, "name": "x", "status": "draft"}]}},
        {"data": {"results": [{"id": 1, "name": 2, "status": "draft"}]}},
    ],
)
@respx.mock
def test_rejects_malformed_list_responses(
    response,
    config,
    make_retrying_http,
):
    respx.get(
        "https://listmonk.example/api/campaigns",
        params={"per_page": "all", "no_body": "true"},
    ).mock(return_value=httpx.Response(200, json=response))
    listmonk = client(config, make_retrying_http)

    with pytest.raises(ListmonkError, match="Malformed"):
        listmonk.list_campaigns()


@respx.mock
def test_rejects_invalid_json_response(config, make_retrying_http):
    respx.get(
        "https://listmonk.example/api/campaigns",
        params={"per_page": "all", "no_body": "true"},
    ).mock(return_value=httpx.Response(200, text="not-json"))
    listmonk = client(config, make_retrying_http)

    with pytest.raises(ListmonkError, match="invalid JSON"):
        listmonk.list_campaigns()


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"id": True, "name": "post-key", "status": "draft"},
        {"id": 2, "name": "post-key", "status": "draft"},
        {"id": 1, "name": 3, "status": "draft"},
        {"id": 1, "name": "post-key"},
    ],
)
@respx.mock
def test_rejects_malformed_full_campaign(
    data,
    config,
    make_retrying_http,
):
    respx.get("https://listmonk.example/api/campaigns/1").mock(
        return_value=httpx.Response(200, json={"data": data})
    )
    listmonk = client(config, make_retrying_http)

    with pytest.raises(ListmonkError, match="Malformed|malformed"):
        listmonk.get_campaign(1)


@pytest.mark.parametrize("campaign_id", [True, 0, -1])
@respx.mock
def test_rejects_malformed_create_response(
    campaign_id,
    config,
    make_retrying_http,
):
    respx.post("https://listmonk.example/api/campaigns").mock(
        return_value=httpx.Response(200, json={"data": {"id": campaign_id}})
    )
    listmonk = client(config, make_retrying_http)

    with pytest.raises(ListmonkError, match="Malformed"):
        listmonk.create_campaign(post())
