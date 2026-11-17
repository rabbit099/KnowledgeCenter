"""HTML (e-mail bodies) to plain text with the standard library parser.

Nothing is executed or fetched: the parser only tokenizes. Scripts, styles and other
non-content elements are dropped, block elements become line breaks, entities are decoded,
links keep only their visible text (URLs are not followed and not added).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

SKIP = frozenset({"script", "style", "head", "title", "template", "noscript", "svg", "object"})
BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "fieldset",
        "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
        "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tr", "ul",
    }
)  # fmt: skip
CELL = frozenset({"td", "th"})
VOID = frozenset({"br", "hr", "img", "meta", "link", "input", "area", "base", "col", "wbr"})


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP:
            if tag not in VOID:
                self.skip_depth += 1
            return
        if tag in BLOCK:
            self.out.append("\n")
        elif tag in CELL:
            self.out.append(" | ")
        if tag == "li":
            self.out.append("- ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in BLOCK:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.out.append(data)


def html_to_text(html: str) -> str:
    parser = _TextParser()
    parser.feed(html)
    parser.close()
    text = "".join(parser.out).replace("\u00a0", " ")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
