"""Protect feed-owned text from Listmonk's Go-template compiler."""

from __future__ import annotations

_OPEN_TEMPLATE_LITERAL = r'{{ printf "\x7b\x7b" }}'
_CLOSE_TEMPLATE_LITERAL = r'{{ printf "\x7d\x7d" }}'


def protect_template_delimiters(value: str) -> str:
    """Render feed-owned double braces literally after template execution."""
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
