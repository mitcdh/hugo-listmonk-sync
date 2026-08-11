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
        "{{ .Campaign.Attribs.newsletter.headerKicker | Safe }}\n\n"
        "{{ with .Campaign.Attribs.post.title }}{{ . | Safe }}"
        "{{ else }}{{ $.Campaign.Subject | Safe }}{{ end }}\n"
        "==========\n\n"
        "{{ .Campaign.Attribs.post.description | Safe }}\n\n"
        "{{ .Campaign.Attribs.newsletter.author | Safe }} · "
        '{{ .Campaign.Attribs.post.date | toDate "2006-01-02T15:04:05Z07:00" '
        '| date "2 January 2006" }} · '
        "{{ .Campaign.Attribs.post.readingTime | Safe }}"
    )
    assert "Article heading\n---------------" in rendered
    assert "full report: https://example.com/report" in rendered
    assert "- First item" in rendered
    assert "  - Nested" in rendered
    assert "> A useful quotation." in rendered
    assert "```\nanswer = 42\n```" in rendered
    assert "Image: Results chart — https://example.com/chart.png" in rendered
    assert "divider.png" not in rendered
    assert "tracking" not in rendered
    assert "READ THE FULL POST\n{{ .Campaign.Attribs.post.url | Safe }}" in rendered
    assert (
        "Published by {{ .Campaign.Attribs.newsletter.author | Safe }}\n"
        "{{ .Campaign.Attribs.newsletter.address | Safe }}"
    ) in rendered
    assert "Unsubscribe: {{ UnsubscribeURL . | Safe }}" in rendered
    assert "View online: {{ MessageURL . | Safe }}" in rendered
    assert (
        "Visit {{ .Campaign.Attribs.newsletter.siteName | Safe }}: "
        "{{ .Campaign.Attribs.newsletter.baseURL | Safe }}"
    ) in rendered
    assert "Publisher Name" not in rendered
    assert "Post description." not in rendered


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
    def broken_converter(_html, **_kwargs):
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
    assert rendered.count("{{ UnsubscribeURL . | Safe }}") == 1
    assert rendered.count("{{ MessageURL . | Safe }}") == 1


def test_malformed_optional_publication_date_is_omitted(config):
    post = make_post(html="<p>Body</p>", attributes={"date": "not-a-date"})

    rendered = PlainTextRenderer(config).render(post)

    assert "not-a-date" not in rendered
    assert "{{ .Campaign.Attribs.post.readingTime | Safe }}" in rendered


def test_strips_web_controls_and_internal_fragment_navigation():
    converted = plaintext._convert_html(
        "<p>Claim<sup>"
        '<a class="footnote-ref" href="#fn:5">5</a>'
        "</sup>.</p>"
        '<figure class="code-block"><figcaption>'
        '<span class="code-block__label">example.yaml</span>'
        '<a class="code-block__source" href="https://example.com/source">'
        "Source</a>"
        '<button type="button" hidden>Wrap</button>'
        '<button type="button" hidden>Copy</button>'
        "</figcaption><pre><code>"
        '<span class="line"><span class="ln">'
        '<a class="lnlinks" href="#code-1-line-1">1</a></span>'
        '<span class="cl">---</span></span>\n'
        '<span class="line"><span class="ln">'
        '<a class="lnlinks" href="#code-1-line-2">2</a></span>'
        '<span class="cl">title: Example</span></span>'
        "</code></pre>"
        '<span class="screen-reader-text" data-code-status>Copied</span>'
        "</figure><h2>References</h2>"
        '<div class="footnotes"><hr><ol><li><p>Reference '
        '<a href="https://example.com/ref">https://example.com/ref</a>&nbsp;'
        '<a class="footnote-backref" href="#fnref:5">↩︎</a>'
        "</p></li></ol></div>"
    )

    assert "Claim[5]." in converted
    assert "example.yaml" in converted
    assert "Source: https://example.com/source" in converted
    assert "```\n---\ntitle: Example\n```" in converted
    assert "References\n----------\n\n1. Reference https://example.com/ref" in converted
    assert "Wrap" not in converted
    assert "Copy" not in converted
    assert "Copied" not in converted
    assert "(#fn" not in converted
    assert "(#code-" not in converted
    assert "↩" not in converted


def test_preserves_iframe_destination_as_plain_text_link():
    converted = plaintext._convert_html(
        '<iframe title="Demonstration" src="https://www.youtube.com/embed/abc"></iframe>'
    )

    assert converted == "Video: Demonstration — https://www.youtube.com/embed/abc"


def test_resolves_relative_article_links_and_images():
    converted = plaintext._convert_html(
        '<p><a href="../../report">Report</a></p>'
        '<img alt="Chart" src="/images/chart.png">',
        base_url="https://blog.example/posts/example/",
    )

    assert "Report: https://blog.example/report" in converted
    assert "Image: Chart — https://blog.example/images/chart.png" in converted


def test_aligns_plain_text_table_columns_to_cell_contents():
    converted = plaintext._convert_html(
        "<p><strong>Example table</strong></p>"
        "<table><thead><tr><th>Name</th><th>Value</th></tr></thead>"
        "<tbody><tr><td>Short</td><td>Longer value</td></tr>"
        "<tr><td>A</td><td>2</td></tr></tbody></table>"
    )

    assert converted == (
        "Example table\n\n"
        "Name   Value\n"
        "-----  ------------\n"
        "Short  Longer value\n"
        "A      2"
    )
    assert "|" not in converted
    assert "**" not in converted
    assert all(len(line) <= 80 for line in converted.splitlines())


def test_omits_table_when_aligned_line_would_exceed_80_characters():
    long_value = "x" * 72
    converted = plaintext._convert_html(
        "<p>Before.</p><table><tr><th>Key</th><th>Value</th></tr>"
        f"<tr><td>example</td><td>{long_value}</td></tr></table>"
        "<p>After.</p>"
    )

    assert converted == (
        "Before.\n\n"
        "Table omitted: it exceeds 80 characters per line. See the full article.\n\n"
        "After."
    )
    assert long_value not in converted
