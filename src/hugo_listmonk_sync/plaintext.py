"""Structured plain-text campaign body generation."""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass
from typing import Any

from hugo_listmonk_sync.config import Config
from hugo_listmonk_sync.errors import PlainTextError
from hugo_listmonk_sync.feed import FeedPost
from hugo_listmonk_sync.template_safety import protect_template_delimiters
from hugo_listmonk_sync.timestamps import parse_aware_iso8601
from hugo_listmonk_sync.urls import resolve_content_url

logger = logging.getLogger(__name__)

_DIVIDER = "-" * 64
_TABLE_MAX_WIDTH = 80
_TABLE_OMITTED = (
    "Table omitted: it exceeds 80 characters per line. See the full article."
)
_HEADER_KICKER_TEMPLATE = "{{ .Campaign.Attribs.newsletter.headerKicker | Safe }}"
_TITLE_TEMPLATE = (
    "{{ with .Campaign.Attribs.post.title }}{{ . | Safe }}"
    "{{ else }}{{ $.Campaign.Subject | Safe }}{{ end }}"
)
_DESCRIPTION_TEMPLATE = "{{ .Campaign.Attribs.post.description | Safe }}"
_AUTHOR_TEMPLATE = "{{ .Campaign.Attribs.newsletter.author | Safe }}"
_ADDRESS_TEMPLATE = "{{ .Campaign.Attribs.newsletter.address | Safe }}"
_SITE_NAME_TEMPLATE = "{{ .Campaign.Attribs.newsletter.siteName | Safe }}"
_BASE_URL_TEMPLATE = "{{ .Campaign.Attribs.newsletter.baseURL | Safe }}"
_POST_URL_TEMPLATE = "{{ .Campaign.Attribs.post.url | Safe }}"
_READING_TIME_TEMPLATE = "{{ .Campaign.Attribs.post.readingTime | Safe }}"
_DATE_TEMPLATE = (
    '{{ .Campaign.Attribs.post.date | toDate "2006-01-02T15:04:05Z07:00" '
    '| date "2 January 2006" }}'
)


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
            _HEADER_KICKER_TEMPLATE,
            _heading(title),
        ]
        if description is not None:
            header.append(_DESCRIPTION_TEMPLATE)

        byline = _byline(post, resolved)
        if byline:
            header.append(byline)

        sections = [*header, _DIVIDER, self._article_body(post)]
        if post_url is not None:
            sections.append(f"READ THE FULL POST\n{_POST_URL_TEMPLATE}")

        footer: list[str] = []
        if resolved.author is not None:
            footer.append(f"Published by {_AUTHOR_TEMPLATE}")
        if resolved.address is not None:
            footer.append(_ADDRESS_TEMPLATE)
        footer.extend(
            [
                "Unsubscribe: {{ UnsubscribeURL . | Safe }}",
                "View online: {{ MessageURL . | Safe }}",
            ]
        )
        if resolved.base_url is not None:
            site_label = (
                f"Visit {_SITE_NAME_TEMPLATE}"
                if resolved.site_name is not None
                else "Visit the blog"
            )
            footer.append(f"{site_label}: {_BASE_URL_TEMPLATE}")

        sections.extend([_DIVIDER, "\n".join(footer)])
        return "\n\n".join(section for section in sections if section).strip() + "\n"

    @staticmethod
    def _article_body(post: FeedPost) -> str:
        if post.html is not None:
            try:
                converted = _convert_html(
                    post.html,
                    base_url=_attribute_string(post, "url"),
                )
            except _HtmlConversionError as exc:
                logger.warning(
                    "Could not convert HTML for campaign %r; using feed text: %s",
                    post.name,
                    exc,
                )
            else:
                if converted:
                    return protect_template_delimiters(converted)
                logger.warning(
                    "HTML conversion for campaign %r produced no text; using feed text",
                    post.name,
                )

        if post.text is not None and post.text.strip():
            return protect_template_delimiters(_normalize_text(post.text))
        raise PlainTextError(
            f"Campaign {post.name!r} has no usable HTML conversion or text fallback"
        )


class _HtmlConversionError(Exception):
    """Internal wrapper for converter availability and execution failures."""


def _convert_html(html: str, *, base_url: str | None = None) -> str:  # noqa: C901
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
            original_destination = _tag_string(el, "href")
            classes = _tag_classes(el)

            if "footnote-backref" in classes or "lnlinks" in classes:
                rendered = ""
            elif "footnote-ref" in classes:
                rendered = f"[{label}]" if label else ""
            elif original_destination is None or original_destination.startswith("#"):
                rendered = label
            else:
                destination = resolve_content_url(original_destination, base_url)
                if not label or label in {original_destination, destination}:
                    rendered = destination
                else:
                    rendered = f"{label}: {destination}"
            return rendered

        def convert_table(
            self,
            el: Any,
            text: str,
            parent_tags: set[str],
        ) -> str:
            del text
            return _render_table(self, el, parent_tags)

        def convert_button(
            self,
            el: Any,
            text: str,
            parent_tags: set[str],
        ) -> str:
            del el, text, parent_tags
            return ""

        def convert_b(
            self,
            el: Any,
            text: str,
            parent_tags: set[str],
        ) -> str:
            del el, parent_tags
            return text

        def convert_em(
            self,
            el: Any,
            text: str,
            parent_tags: set[str],
        ) -> str:
            del el, parent_tags
            return text

        convert_i = convert_em
        convert_strong = convert_b

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
            source = resolve_content_url(source, base_url)
            title = _tag_string(el, "title") or "Embedded content"
            kind = "Video" if _is_video_embed(source) else "Embedded content"
            return f"\n\n{kind}: {_normalize_inline(title)} — {source}\n\n"

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
                return f"Image: {alt}"
            source = resolve_content_url(source, base_url)
            return f"Image: {alt} — {source}"

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


def _render_table(converter: Any, table: Any, parent_tags: set[str]) -> str:
    rows: list[list[str]] = []
    first_row_is_header = False
    for row in table.find_all("tr"):
        if row.find_parent("table") is not table:
            continue
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        if not rows:
            first_row_is_header = all(cell.name == "th" for cell in cells)
        rendered_row: list[str] = []
        for cell in cells:
            rendered_row.append(_render_table_cell(converter, cell, parent_tags))
            rendered_row.extend("" for _ in range(_colspan(cell) - 1))
        rows.append(rendered_row)

    caption = table.find("caption", recursive=False)
    caption_text = (
        _render_table_cell(converter, caption, parent_tags)
        if caption is not None
        else ""
    )
    if not rows:
        return f"\n\n{caption_text}\n\n" if caption_text else ""

    column_count = max(len(row) for row in rows)
    for row in rows:
        row.extend("" for _ in range(column_count - len(row)))
    widths = [
        max(3, *(len(row[index]) for row in rows)) for index in range(column_count)
    ]
    lines: list[str] = []
    if first_row_is_header:
        lines.extend(
            [
                _table_line(rows[0], widths),
                _table_line(widths, widths, separator=True),
            ]
        )
        lines.extend(_table_line(row, widths) for row in rows[1:])
    else:
        lines.extend(_table_line(row, widths) for row in rows)

    rendered = (
        _TABLE_OMITTED
        if any(len(line) > _TABLE_MAX_WIDTH for line in lines)
        else "\n".join(lines)
    )
    if caption_text:
        rendered = f"{caption_text}\n\n{rendered}"
    return f"\n\n{rendered}\n\n"


def _render_table_cell(
    converter: Any,
    cell: Any,
    parent_tags: set[str],
) -> str:
    clone = copy.copy(cell)
    clone.name = "span"
    rendered = converter.process_tag(
        clone,
        parent_tags=set(parent_tags) | {"table", "tr", "_inline"},
    )
    return _normalize_inline(rendered)


def _colspan(cell: Any) -> int:
    value = cell.get("colspan")
    if isinstance(value, str) and value.isdigit():
        return max(1, min(1000, int(value)))
    return 1


def _table_line(
    values: list[str] | list[int],
    widths: list[int],
    *,
    separator: bool = False,
) -> str:
    if separator:
        cells = ["-" * width for width in widths]
    else:
        cells = [
            str(value).ljust(width) for value, width in zip(values, widths, strict=True)
        ]
    return "  ".join(cells).rstrip()


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
        values.append(_AUTHOR_TEMPLATE)

    raw_date = _attribute_string(post, "date")
    if raw_date is not None:
        try:
            parse_aware_iso8601(raw_date)
        except ValueError:
            logger.warning(
                "Campaign %r has malformed optional publication date; omitting it",
                post.name,
            )
        else:
            values.append(_DATE_TEMPLATE)

    reading_time = _attribute_string(post, "readingTime")
    if reading_time is not None:
        values.append(_READING_TIME_TEMPLATE)
    return " · ".join(values)


def _heading(title: str) -> str:
    return f"{_TITLE_TEMPLATE}\n{'=' * max(3, len(title))}"


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
