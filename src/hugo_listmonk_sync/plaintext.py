"""Structured plain-text campaign body generation."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from hugo_listmonk_sync.config import Config
from hugo_listmonk_sync.errors import PlainTextError
from hugo_listmonk_sync.feed import FeedPost
from hugo_listmonk_sync.timestamps import parse_aware_iso8601

logger = logging.getLogger(__name__)

_DIVIDER = "-" * 64
_OPEN_TEMPLATE_LITERAL = r'{{ printf "\x7b\x7b" }}'
_CLOSE_TEMPLATE_LITERAL = r'{{ printf "\x7d\x7d" }}'


@dataclass(frozen=True, slots=True)
class NewsletterPresentation:
    """Resolved presentation values shared by HTML and plain-text bodies."""

    header_kicker: str
    author: str | None
    address: str | None
    site_name: str | None
    base_url: str | None

    def as_attributes(self) -> dict[str, str]:
        """Return non-empty values in the campaign attribute wire shape."""
        values = {
            "headerKicker": self.header_kicker,
            "author": self.author,
            "address": self.address,
            "siteName": self.site_name,
            "baseURL": self.base_url,
        }
        return {key: value for key, value in values.items() if value is not None}


class PlainTextRenderer:
    """Generate a structured Listmonk alternate body for one feed post."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def resolve_presentation(self, post: FeedPost) -> NewsletterPresentation:
        """Resolve per-post overrides over environment-backed defaults."""
        return NewsletterPresentation(
            header_kicker=(
                _attribute_string(post, "headerKicker")
                or self._config.newsletter_header_kicker
            ),
            author=(
                _attribute_string(post, "author") or self._config.newsletter_author
            ),
            address=(
                _attribute_string(post, "address") or self._config.newsletter_address
            ),
            site_name=(
                _attribute_string(post, "siteName") or self._config.newsletter_site_name
            ),
            base_url=_without_trailing_slash(
                _attribute_string(post, "baseURL") or self._config.newsletter_base_url
            ),
        )

    def render(
        self,
        post: FeedPost,
        presentation: NewsletterPresentation | None = None,
    ) -> str:
        """Render the complete alternate body, including metadata and footer."""
        resolved = presentation or self.resolve_presentation(post)
        title = _attribute_string(post, "title") or post.subject
        description = _attribute_string(post, "description")
        post_url = _attribute_string(post, "url")

        header = [
            _protect_template_delimiters(resolved.header_kicker),
            _heading(title),
        ]
        if description is not None:
            header.append(_protect_template_delimiters(description))

        byline = _byline(post, resolved)
        if byline:
            header.append(byline)

        sections = [*header, _DIVIDER, self._article_body(post)]
        if post_url is not None:
            sections.append(
                f"READ THE FULL POST\n{_protect_template_delimiters(post_url)}"
            )

        footer: list[str] = []
        if resolved.author is not None:
            footer.append(
                f"Published by {_protect_template_delimiters(resolved.author)}"
            )
        if resolved.address is not None:
            footer.append(_protect_template_delimiters(resolved.address))
        footer.extend(
            [
                "Unsubscribe: {{ UnsubscribeURL }}",
                "View online: {{ MessageURL }}",
            ]
        )
        if resolved.base_url is not None:
            site_label = (
                f"Visit {resolved.site_name}"
                if resolved.site_name is not None
                else "Visit the blog"
            )
            footer.append(
                f"{_protect_template_delimiters(site_label)}: "
                f"{_protect_template_delimiters(resolved.base_url)}"
            )

        sections.extend([_DIVIDER, "\n".join(footer)])
        return "\n\n".join(section for section in sections if section).strip() + "\n"

    @staticmethod
    def _article_body(post: FeedPost) -> str:
        if post.html is not None:
            try:
                converted = _convert_html(post.html)
            except _HtmlConversionError as exc:
                logger.warning(
                    "Could not convert HTML for campaign %r; using feed text: %s",
                    post.name,
                    exc,
                )
            else:
                if converted:
                    return _protect_template_delimiters(converted)
                logger.warning(
                    "HTML conversion for campaign %r produced no text; using feed text",
                    post.name,
                )

        if post.text is not None and post.text.strip():
            return _protect_template_delimiters(_normalize_text(post.text))
        raise PlainTextError(
            f"Campaign {post.name!r} has no usable HTML conversion or text fallback"
        )


class _HtmlConversionError(Exception):
    """Internal wrapper for converter availability and execution failures."""


def _convert_html(html: str) -> str:  # noqa: C901
    try:
        from markdownify import MarkdownConverter  # noqa: PLC0415
    except ImportError as exc:
        raise _HtmlConversionError("markdownify is unavailable") from exc

    class NewsletterConverter(MarkdownConverter):
        """Render links and meaningful images in an explicit text form."""

        def convert_a(
            self,
            el: Any,
            text: str,
            parent_tags: set[str],
        ) -> str:
            del parent_tags
            label = _normalize_inline(text)
            destination = _tag_string(el, "href")
            classes = _tag_classes(el)

            if "footnote-backref" in classes or "lnlinks" in classes:
                rendered = ""
            elif "footnote-ref" in classes:
                rendered = f"[{label}]" if label else ""
            elif destination is None or destination.startswith("#"):
                rendered = label
            elif not label or label == destination:
                rendered = destination
            else:
                rendered = f"[{label}] ({destination})"
            return rendered

        def convert_button(
            self,
            el: Any,
            text: str,
            parent_tags: set[str],
        ) -> str:
            del el, text, parent_tags
            return ""

        def convert_span(
            self,
            el: Any,
            text: str,
            parent_tags: set[str],
        ) -> str:
            del parent_tags
            if "ln" in _tag_classes(el) or el.has_attr("data-code-status"):
                return ""
            return text

        def convert_hr(
            self,
            el: Any,
            text: str,
            parent_tags: set[str],
        ) -> str:
            del text, parent_tags
            if el.find_parent(class_="footnotes") is not None:
                return ""
            return "\n\n---\n\n"

        def convert_iframe(
            self,
            el: Any,
            text: str,
            parent_tags: set[str],
        ) -> str:
            del text, parent_tags
            source = _tag_string(el, "src")
            if source is None:
                return ""
            title = _tag_string(el, "title") or "Embedded content"
            kind = "Video" if _is_video_embed(source) else "Embedded content"
            return f"\n\n[{kind}: {_normalize_inline(title)}] ({source})\n\n"

        def convert_img(
            self,
            el: Any,
            text: str,
            parent_tags: set[str],
        ) -> str:
            del text, parent_tags
            alt = _tag_string(el, "alt")
            if alt is None:
                return ""
            source = _tag_string(el, "src")
            if source is None:
                return f"[Image: {alt}]"
            return f"[Image: {alt}] ({source})"

        def convert_script(
            self,
            el: Any,
            text: str,
            parent_tags: set[str],
        ) -> str:
            del el, text, parent_tags
            return ""

        def convert_style(
            self,
            el: Any,
            text: str,
            parent_tags: set[str],
        ) -> str:
            del el, text, parent_tags
            return ""

    try:
        converted = NewsletterConverter(
            autolinks=False,
            bullets="-",
            heading_style="UNDERLINED",
        ).convert(html)
    except Exception as exc:
        raise _HtmlConversionError(str(exc) or type(exc).__name__) from exc
    return _normalize_text(converted)


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


def _tag_classes(tag: Any) -> frozenset[str]:
    value = tag.get("class")
    if isinstance(value, str):
        return frozenset(value.split())
    if isinstance(value, (list, tuple)):
        return frozenset(item for item in value if isinstance(item, str))
    return frozenset()


def _is_video_embed(source: str) -> bool:
    lowered = source.casefold()
    return "youtube.com/" in lowered or "youtu.be/" in lowered


def _byline(post: FeedPost, presentation: NewsletterPresentation) -> str:
    values: list[str] = []
    if presentation.author is not None:
        values.append(_protect_template_delimiters(presentation.author))

    raw_date = _attribute_string(post, "date")
    if raw_date is not None:
        try:
            parsed_date = parse_aware_iso8601(raw_date)
        except ValueError:
            logger.warning(
                "Campaign %r has malformed optional publication date; omitting it",
                post.name,
            )
        else:
            values.append(f"{parsed_date.day} {parsed_date:%B %Y}")

    reading_time = _attribute_string(post, "readingTime")
    if reading_time is not None:
        values.append(_protect_template_delimiters(reading_time))
    return " · ".join(values)


def _heading(title: str) -> str:
    return f"{_protect_template_delimiters(title)}\n{'=' * max(3, len(title))}"


def _without_trailing_slash(value: str | None) -> str | None:
    if value is None:
        return None
    return value.rstrip("/")


def _normalize_inline(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_text(value: str) -> str:
    normalized = (
        value.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u00a0", " ")
        .replace("\u202f", " ")
    )
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _protect_template_delimiters(value: str) -> str:
    protected: list[str] = []
    index = 0
    while index < len(value):
        if value.startswith("{{", index):
            protected.append(_OPEN_TEMPLATE_LITERAL)
            index += 2
        elif value.startswith("}}", index):
            protected.append(_CLOSE_TEMPLATE_LITERAL)
            index += 2
        else:
            protected.append(value[index])
            index += 1
    return "".join(protected)
