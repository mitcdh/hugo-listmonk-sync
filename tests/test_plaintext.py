from __future__ import annotations

import pytest

from hugo_listmonk_sync import plaintext
from hugo_listmonk_sync.errors import PlainTextError
from hugo_listmonk_sync.feed import FeedPost
from hugo_listmonk_sync.plaintext import PlainTextRenderer


def make_post(*, html=None, text=None, attributes=None):
    metadata = {
        "key": "post-key",
        "title": "Post title",
        "description": "Post description.",
        "date": "2026-08-09T07:34:55Z",
        "readingTime": "12 min read",
        "url": "https://blog.example/posts/post-key/",
    }
    metadata.update(attributes or {})
    return FeedPost(
        name="post-key",
        subject="Post title",
        content=html or "<p>Campaign content</p>",
        attributes=metadata,
        html=html,
        text=text,
    )


def test_renders_structured_plain_text_and_shared_presentation(make_config):
    renderer = PlainTextRenderer(
        make_config(
            newsletter_author="Publisher Name",
            newsletter_address="Postal address",
            newsletter_site_name="Example Blog",
            newsletter_base_url="https://blog.example/",
        )
    )
    post = make_post(
        html="""
            <h2>Article heading</h2>
            <p>Read the <a href="https://example.com/report">full report</a>.</p>
            <ul><li>First item</li><li>Second item<ul><li>Nested</li></ul></li></ul>
            <blockquote><p>A useful quotation.</p></blockquote>
            <pre><code>answer = 42</code></pre>
            <img src="https://example.com/chart.png" alt="Results chart">
            <img src="https://example.com/divider.png" alt="">
            <script>alert("tracking")</script>
            <style>.tracking { display: none; }</style>
        """,
    )

    presentation = renderer.resolve_presentation(post)
    rendered = renderer.render(post, presentation)

    assert presentation.as_attributes() == {
        "headerKicker": "NEW BLOG POST",
        "author": "Publisher Name",
        "address": "Postal address",
        "siteName": "Example Blog",
        "baseURL": "https://blog.example",
    }
    assert rendered.startswith(
        "NEW BLOG POST\n\n"
        "Post title\n==========\n\n"
        "Post description.\n\n"
        "Publisher Name · 9 August 2026 · 12 min read"
    )
    assert "Article heading\n---------------" in rendered
    assert "[full report] (https://example.com/report)" in rendered
    assert "- First item" in rendered
    assert "  - Nested" in rendered
    assert "> A useful quotation." in rendered
    assert "```\nanswer = 42\n```" in rendered
    assert "[Image: Results chart] (https://example.com/chart.png)" in rendered
    assert "divider.png" not in rendered
    assert "tracking" not in rendered
    assert "READ THE FULL POST\nhttps://blog.example/posts/post-key/" in rendered
    assert "Published by Publisher Name\nPostal address" in rendered
    assert "Unsubscribe: {{ UnsubscribeURL }}" in rendered
    assert "View online: {{ MessageURL }}" in rendered
    assert "Visit Example Blog: https://blog.example" in rendered


def test_post_metadata_overrides_environment_presentation(make_config):
    renderer = PlainTextRenderer(
        make_config(
            newsletter_header_kicker="CONFIG KICKER",
            newsletter_author="Config author",
            newsletter_address="Config address",
            newsletter_site_name="Config site",
            newsletter_base_url="https://config.example",
        )
    )
    post = make_post(
        html="<p>Body</p>",
        attributes={
            "headerKicker": "FEED KICKER",
            "author": "Feed author",
            "address": "Feed address",
            "siteName": "Feed site",
            "baseURL": "https://feed.example/",
        },
    )

    presentation = renderer.resolve_presentation(post)

    assert presentation.as_attributes() == {
        "headerKicker": "FEED KICKER",
        "author": "Feed author",
        "address": "Feed address",
        "siteName": "Feed site",
        "baseURL": "https://feed.example",
    }


def test_conversion_failure_uses_feed_text(monkeypatch, config):
    def broken_converter(_html):
        raise plaintext._HtmlConversionError("broken converter")

    monkeypatch.setattr(plaintext, "_convert_html", broken_converter)
    post = make_post(html="<p>Ignored</p>", text="Fallback article\n\n- Item")

    rendered = PlainTextRenderer(config).render(post)

    assert "Fallback article\n\n- Item" in rendered
    assert "Ignored" not in rendered


def test_missing_conversion_and_fallback_fails_post(config):
    post = make_post(html=None, text=None)

    with pytest.raises(PlainTextError, match="no usable HTML conversion"):
        PlainTextRenderer(config).render(post)


def test_dynamic_double_braces_are_safe_but_listmonk_expressions_remain(config):
    post = make_post(
        html="<pre><code>{{ .Campaign.Secret }}</code></pre>",
        attributes={
            "title": "Literal {{ title }}",
            "description": "Literal }} description",
        },
    )

    rendered = PlainTextRenderer(config).render(post)

    assert "{{ .Campaign.Secret }}" not in rendered
    assert "{{ title }}" not in rendered
    assert '{{ printf "\\x7b\\x7b" }}' in rendered
    assert '{{ printf "\\x7d\\x7d" }}' in rendered
    assert rendered.count("{{ UnsubscribeURL }}") == 1
    assert rendered.count("{{ MessageURL }}") == 1


def test_malformed_optional_publication_date_is_omitted(config):
    post = make_post(html="<p>Body</p>", attributes={"date": "not-a-date"})

    rendered = PlainTextRenderer(config).render(post)

    assert "not-a-date" not in rendered
    assert "12 min read" in rendered
