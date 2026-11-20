"""Highlighted snippets without trusting PostgreSQL's output (CLAUDE.md 7).

ts_headline does not escape HTML. It marks hits with two private-use characters (sentinels)
that can never come from a document (the indexer removes them from the text); the whole snippet
is then HTML-escaped here, and only afterwards are the sentinels replaced with <mark>.
"""

from __future__ import annotations

from markupsafe import Markup, escape

START, STOP = "", ""
HEADLINE_OPTIONS = (
    f"StartSel={START}, StopSel={STOP}, MaxWords=35, MinWords=15, ShortWord=2, "
    'MaxFragments=2, FragmentDelimiter=" … "'
)


def strip_sentinels(value: str) -> str:
    return value.replace(START, " ").replace(STOP, " ")


def to_html(snippet: str) -> Markup:
    escaped = str(escape(snippet))
    return Markup(escaped.replace(START, "<mark>").replace(STOP, "</mark>"))
