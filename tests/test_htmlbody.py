from __future__ import annotations

from hugo_listmonk_sync.feed import FeedPost
from hugo_listmonk_sync.htmlbody import HtmlBodyRenderer


def make_post(content: str, *, attributes=None) -> FeedPost:
    return FeedPost(
        name="post-key",
        subject="Post title",
        content=content,
        attributes=attributes or {},
        html=content,
        text="Post text",
    )


def test_makes_article_html_email_safe_and_resolves_relative_urls(config):
    post = make_post(
        '<p><a href="/report">Report</a>'
        '<img src="images/chart.png" alt="Chart"></p>'
        '<button class="code-block__control" hidden>Copy</button>'
        "<span data-code-status>Copied</span>"
        '<details class="code-details"><summary>Show example</summary>'
        "<pre><code>answer = 42</code></pre></details>"
        '<iframe title="Demonstration" src="/videos/demo"></iframe>'
        '<table style="width: 100%; color: red"><tr><th>Key</th>'
        "<td>Value</td></tr></table>"
        '<script>alert("bad")</script><style>.bad { color: red; }</style>',
        attributes={"url": "https://blog.example/posts/post-key/"},
    )

    rendered = HtmlBodyRenderer(config).render(post, "html")

    assert 'href="https://blog.example/report"' in rendered
    assert 'src="https://blog.example/posts/post-key/images/chart.png"' in rendered
    assert "<details" not in rendered
    assert '<div class="code-details email-expanded-details">' in rendered
    assert (
        '<p class="email-expanded-details__summary"><strong>Show example</strong></p>'
        in rendered
    )
    assert "<pre><code>answer = 42</code></pre>" in rendered
    assert "<iframe" not in rendered
    assert (
        '<a href="https://blog.example/videos/demo">Embedded content: Demonstration</a>'
    ) in rendered
    assert "Copy" not in rendered
    assert "Copied" not in rendered
    assert "alert" not in rendered
    assert ".bad" not in rendered
    assert (
        '<table style="width: auto; color: red; border-collapse: collapse; '
        'margin: 26px 0; max-width: 100%; table-layout: auto">'
    ) in rendered
    assert (
        '<th style="padding: 10px 12px; text-align: left; vertical-align: top">Key</th>'
    ) in rendered
    assert (
        '<td style="padding: 10px 12px; text-align: left; vertical-align: top">'
        "Value</td>"
    ) in rendered


def test_preserves_internal_html_fragments_and_non_html_content(config):
    html = '<p><a href="#fn:5">5</a></p>'
    renderer = HtmlBodyRenderer(config)

    assert renderer.render(make_post(html), "html") == html
    assert renderer.render(make_post("**Markdown**"), "markdown") == "**Markdown**"


def test_protects_template_delimiters_decoded_from_article_html(config):
    html = (
        '<pre><code><span class="cp">&#123;&#123;</span> '
        '.Campaign.Secret <span class="cp">&#125;&#125;</span></code></pre>'
    )

    rendered = HtmlBodyRenderer(config).render(make_post(html), "html")

    assert "{{ .Campaign.Secret }}" not in rendered
    assert rendered.count(r'{{ printf "\x7b\x7b" }}') == 1
    assert rendered.count(r'{{ printf "\x7d\x7d" }}') == 1
