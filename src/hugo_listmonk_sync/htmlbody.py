"""Transform browser-oriented article HTML into an e-mail-safe body."""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from hugo_listmonk_sync.config import Config
from hugo_listmonk_sync.feed import FeedPost
from hugo_listmonk_sync.template_safety import protect_template_delimiters
from hugo_listmonk_sync.urls import resolve_content_url

_HTML_CONTENT_TYPES = frozenset({"html", "richtext"})


class HtmlBodyRenderer:
    """Render synchronizer-owned campaign HTML without browser-only controls."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def render(self, post: FeedPost, content_type: str) -> str:
        """Return raw non-HTML content or a sanitized HTML fragment."""
        if content_type not in _HTML_CONTENT_TYPES:
            return post.content
        base_url = _attribute_string(post, "url") or self._config.newsletter_base_url
        return _sanitize_html(post.content, base_url=base_url)


def _sanitize_html(html: str, *, base_url: str | None) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for unwanted in soup.select(
        "script, style, button.code-block__control, [data-code-status], [hidden]"
    ):
        unwanted.decompose()

    _expand_details(soup)
    _replace_iframes(soup, base_url)
    _resolve_urls(soup, base_url)
    _style_tables(soup)
    return protect_template_delimiters(str(soup))


def _expand_details(soup: BeautifulSoup) -> None:
    """Replace disclosure widgets with ordinary always-visible content."""
    for details in soup.find_all("details"):
        summary = details.find("summary", recursive=False)
        details.name = "div"
        details.attrs.pop("open", None)
        details["class"] = " ".join(
            _classes_with(details.get("class"), "email-expanded-details")
        )
        if summary is not None:
            summary.name = "p"
            summary["class"] = "email-expanded-details__summary"
            strong = soup.new_tag("strong")
            for child in list(summary.contents):
                strong.append(child.extract())
            summary.append(strong)


def _replace_iframes(soup: BeautifulSoup, base_url: str | None) -> None:
    """Replace unsupported embedded frames with ordinary links."""
    for iframe in soup.find_all("iframe"):
        source = _tag_string(iframe, "src")
        if source is None:
            iframe.decompose()
            continue
        source = resolve_content_url(source, base_url)
        title = _tag_string(iframe, "title") or "Embedded content"
        kind = "Video" if _is_video_embed(source) else "Embedded content"
        paragraph = soup.new_tag("p")
        paragraph["class"] = "embedded-content-link"
        link = soup.new_tag("a", href=source)
        link.string = f"{kind}: {title}"
        paragraph.append(link)
        iframe.replace_with(paragraph)


def _resolve_urls(soup: BeautifulSoup, base_url: str | None) -> None:
    """Make article-relative links and images usable outside the website."""
    for link in soup.find_all("a", href=True):
        destination = _tag_string(link, "href")
        if destination is not None:
            link["href"] = resolve_content_url(destination, base_url)
    for image in soup.find_all("img", src=True):
        source = _tag_string(image, "src")
        if source is not None:
            image["src"] = resolve_content_url(source, base_url)


def _style_tables(soup: BeautifulSoup) -> None:
    """Inline compact, content-sized table layout for e-mail clients."""
    for table in soup.find_all("table"):
        _merge_style(
            table,
            {
                "border-collapse": "collapse",
                "margin": "26px 0",
                "max-width": "100%",
                "table-layout": "auto",
                "width": "auto",
            },
        )
        for cell in table.find_all(["th", "td"]):
            _merge_style(
                cell,
                {
                    "padding": "10px 12px",
                    "text-align": "left",
                    "vertical-align": "top",
                },
            )


def _merge_style(tag: Any, additions: dict[str, str]) -> None:
    """Merge synchronizer layout declarations over an element's inline CSS."""
    declarations: dict[str, str] = {}
    existing = tag.get("style")
    if isinstance(existing, str):
        for declaration in existing.split(";"):
            name, separator, value = declaration.partition(":")
            if separator and name.strip() and value.strip():
                declarations[name.strip().casefold()] = value.strip()
    declarations.update(additions)
    tag["style"] = "; ".join(f"{name}: {value}" for name, value in declarations.items())


def _attribute_string(post: FeedPost, key: str) -> str | None:
    value = post.attributes.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _tag_string(tag: Any, key: str) -> str | None:
    value = tag.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _classes_with(value: object, additional: str) -> list[str]:
    if isinstance(value, str):
        classes = value.split()
    elif isinstance(value, list):
        classes = [item for item in value if isinstance(item, str)]
    else:
        classes = []
    if additional not in classes:
        classes.append(additional)
    return classes


def _is_video_embed(source: str) -> bool:
    lowered = source.casefold()
    return "youtube.com/" in lowered or "youtu.be/" in lowered
