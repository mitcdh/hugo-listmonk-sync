from __future__ import annotations

import json

import httpx
import pytest
import respx

from hugo_listmonk_sync.errors import FeedError
from hugo_listmonk_sync.feed import FeedClient
from hugo_listmonk_sync.listmonk import ListmonkClient
from hugo_listmonk_sync.reconcile import CycleSummary, Synchronizer


def feed_payload():
    return {
        "schemaVersion": 1,
        "posts": [
            {
                "key": "new-post",
                "title": "New post",
                "html": "<p>New</p>",
                "text": "New",
                "date": "2026-08-09T07:34:55Z",
                "lastmod": "2026-08-09T07:34:55Z",
                "url": "https://blog.example/posts/new-post/",
                "readingTime": 3,
            },
            {
                "key": "draft-post",
                "title": "Revised draft",
                "html": "<p>Revised</p>",
                "text": "Revised",
                "date": "2026-08-09T08:00:00Z",
                "lastmod": "2026-08-09T10:43:49Z",
                "url": "https://blog.example/posts/draft-post/",
                "image": "/images/draft.webp",
            },
        ],
    }


def clients(config, make_retrying_http):
    feed = FeedClient(
        make_retrying_http(),
        url=config.newsletter_json_url,
        name_field=config.campaign_name_field,
        subject_field=config.campaign_subject_field,
        content_field=config.campaign_content_field,
    )
    listmonk = ListmonkClient(
        make_retrying_http(
            auth=httpx.BasicAuth(
                config.listmonk_api_username,
                config.listmonk_api_token,
            )
        ),
        config,
    )
    return feed, listmonk


@respx.mock
def test_full_http_reconciliation_cycle(config, make_retrying_http):
    respx.get(config.newsletter_json_url).mock(
        return_value=httpx.Response(200, json=feed_payload())
    )
    respx.get(
        "https://listmonk.example/api/campaigns",
        params={"per_page": "all", "no_body": "true"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "results": [
                        {
                            "id": 12,
                            "name": "draft-post",
                            "status": "draft",
                        }
                    ]
                }
            },
        )
    )
    create = respx.post("https://listmonk.example/api/campaigns").mock(
        return_value=httpx.Response(200, json={"data": {"id": 13}})
    )
    respx.get("https://listmonk.example/api/campaigns/12").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": 12,
                    "name": "draft-post",
                    "status": "draft",
                    "subject": "Old",
                    "body": "<p>Old</p>",
                    "lists": [{"id": 22, "name": "Preserved"}],
                    "from_email": "Kept <kept@example.test>",
                    "type": "regular",
                    "content_type": "html",
                    "messenger": "email",
                    "template_id": 5,
                    "tags": ["keep"],
                    "headers": [{"X-Keep": "yes"}],
                    "altbody": "Keep me",
                    "send_at": None,
                    "attribs": {
                        "tracking": "keep",
                        "post": {"key": "stale"},
                    },
                }
            },
        )
    )
    update = respx.put("https://listmonk.example/api/campaigns/12").mock(
        return_value=httpx.Response(200, json={"data": {"id": 12}})
    )
    feed, listmonk = clients(config, make_retrying_http)

    summary = Synchronizer(feed, listmonk).run_cycle()

    assert summary == CycleSummary(created=1, updated=1)
    created = json.loads(create.calls[0].request.content)
    assert created["lists"] == [4, 9]
    assert created["content_type"] == "html"
    assert created["body"] == "<p>New</p>"
    assert (
        "{{ .Campaign.Attribs.newsletter.headerKicker | Safe }}" in created["altbody"]
    )
    assert "{{ with .Campaign.Attribs.post.title }}" in created["altbody"]
    assert "\n========\n" in created["altbody"]
    assert "New\n\nREAD THE FULL POST" in created["altbody"]
    assert "Unsubscribe: {{ UnsubscribeURL . | Safe }}" in created["altbody"]
    assert created["attribs"]["post"]["readingTime"] == "3 min read"
    assert created["attribs"]["post"]["lastmod"] == ("2026-08-09T07:34:55Z")
    assert created["attribs"]["newsletter"] == {"headerKicker": "NEW BLOG POST"}
    assert "text" not in created["attribs"]["post"]

    updated = json.loads(update.calls[0].request.content)
    assert updated["name"] == "draft-post"
    assert updated["subject"] == "Revised draft"
    assert updated["body"] == "<p>Revised</p>"
    assert updated["lists"] == [22]
    assert updated["from_email"] == "Kept <kept@example.test>"
    assert updated["template_id"] == 5
    assert updated["tags"] == ["keep"]
    assert updated["altbody"] != "Keep me"
    assert "Revised" in updated["altbody"]
    assert "READ THE FULL POST" in updated["altbody"]
    assert updated["headers"] == [{"X-Keep": "yes"}]
    assert updated["attribs"]["tracking"] == "keep"
    assert updated["attribs"]["post"] == {
        "key": "draft-post",
        "title": "Revised draft",
        "date": "2026-08-09T08:00:00Z",
        "lastmod": "2026-08-09T10:43:49Z",
        "url": "https://blog.example/posts/draft-post/",
        "image": "/images/draft.webp",
    }
    assert updated["attribs"]["newsletter"] == {"headerKicker": "NEW BLOG POST"}


@respx.mock
def test_invalid_feed_causes_no_listmonk_request(config, make_retrying_http):
    payload = feed_payload()
    payload["posts"].append(dict(payload["posts"][0]))
    respx.get(config.newsletter_json_url).mock(
        return_value=httpx.Response(200, json=payload)
    )
    list_route = respx.get(
        "https://listmonk.example/api/campaigns",
        params={"per_page": "all", "no_body": "true"},
    )
    feed, listmonk = clients(config, make_retrying_http)

    with pytest.raises(FeedError, match="Duplicate"):
        Synchronizer(feed, listmonk).run_cycle()

    assert not list_route.called


@respx.mock
def test_malformed_feed_lastmod_causes_no_listmonk_request(
    config,
    make_retrying_http,
):
    payload = feed_payload()
    payload["posts"][0]["lastmod"] = "2026-08-09T07:34:55"
    respx.get(config.newsletter_json_url).mock(
        return_value=httpx.Response(200, json=payload)
    )
    list_route = respx.get(
        "https://listmonk.example/api/campaigns",
        params={"per_page": "all", "no_body": "true"},
    )
    feed, listmonk = clients(config, make_retrying_http)

    with pytest.raises(FeedError, match="lastmod"):
        Synchronizer(feed, listmonk).run_cycle()

    assert not list_route.called


@respx.mock
def test_feed_get_retries_and_rejects_malformed_json(
    config,
    make_retrying_http,
):
    route = respx.get(config.newsletter_json_url).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, text="{broken"),
        ]
    )
    feed = FeedClient(
        make_retrying_http(),
        url=config.newsletter_json_url,
        name_field="key",
        subject_field="title",
        content_field="html",
    )

    with pytest.raises(FeedError, match="valid JSON"):
        feed.fetch()

    assert route.call_count == 2
