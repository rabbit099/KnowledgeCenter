"""Identifiers (numbers and codes) as extra search tokens (ADR-026).

PostgreSQL's text parser splits or glues identifiers in ways that differ between a document
and a query: "UM/17/2026" is one token, so "17/2026" does not find it; "603 388 100" is three
tokens and "603388100" one; "IT-002" becomes "it" and "-002"; "ksiegowosc@example.com" is one
token, so the domain alone does not find it. Each identifier is therefore also written in a
normalised form without separators, at indexing (extra tokens, `ident_terms`) and in the query
(alternative queries, OR-ed with the original). Everything here is lower case and without
diacritics (`fold`), like the `simple_unaccent` configuration.

Rules (what is recognised):
- codes: letters/digits joined by "-" or "/", with at least one digit ("UM/17/2026",
  "725-18-01-126", "IT-002", "EX-PVE0", "FV/2026/11/0042"): every contiguous run of segments
  joined ("um17", "172026", "um172026", ...) and the single segments;
- digit groups separated by spaces or dashes, at least 6 digits, optionally with "+"
  ("603 388 100", "+48 603 388 100", "725 18 01 126"): every contiguous run of groups joined;
  in a query starting with "+", also the variant without the country code;
- e-mail addresses: the local part (and its pieces), the domain and its parent domains;
- host names ("intranet.example.com"): the name and its parent domains;
- IPv4 addresses: the address and its first three octets ("192.168.10").

What does not work (by design, docs/en/search.md): a part of one segment ("0042" in "00042"),
prefixes ("7251*"), runs that do not end at a separator ("3388" in "603 388 100"), letters and
digits separated only by spaces ("UM 17 2026"), other country prefixes ("0048"), IPv6, ranges.
"""

from __future__ import annotations

import re
import unicodedata

MAX_SEGMENTS = 8  # longer chains: only the whole identifier joined
MAX_TOKENS = 5000  # per text part; beyond that the rest is ignored

_FOLD = str.maketrans({"ł": "l", "Ł": "l", "đ": "d", "ø": "o", "ß": "ss"})
_CODE = re.compile(r"(?<![\w@.])[^\W_]+(?:[-/][^\W_]+)+(?![\w@])")
_DIGIT_GROUPS = re.compile(r"(?<![\w+.])\+?\d{1,4}(?:[ \u00a0-]\d{2,4}){1,5}(?![\w])")
_EMAIL = re.compile(r"(?<![\w.+-])([\w.+-]+)@((?:[\w-]+\.)+[a-zA-Z]{2,})(?![\w-])")
_HOST = re.compile(
    r"(?<![\w@.-])((?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,})(?![\w-])"
)
_IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
_SPLIT = re.compile(r"[-/ \u00a0]")
_PLUS_DIGITS = re.compile(r"(?<![\w+])\+(\d{6,15})(?![\w])")


def fold(text: str) -> str:
    """Lower case without diacritics (like the unaccent dictionary)."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_FOLD).lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _runs(segments: list[str], *, singles: bool) -> list[str]:
    if len(segments) > MAX_SEGMENTS:
        return ["".join(segments)]
    shortest = 1 if singles else 2
    return [
        "".join(segments[i:j])
        for i in range(len(segments))
        for j in range(i + shortest, len(segments) + 1)
        if j - i >= 2 or len(segments[i]) >= 2
    ]


def _has_digit(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def _domains(domain: str) -> list[str]:
    labels = domain.split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)]


def index_tokens(text: str) -> list[str]:
    """Extra tokens for a text (deduplicated, in order of appearance, at most MAX_TOKENS)."""
    folded = fold(text)
    found: dict[str, None] = {}

    def add(tokens: list[str]) -> None:
        for token in tokens:
            if token and len(found) < MAX_TOKENS:
                found.setdefault(token, None)

    for match in _CODE.finditer(folded):
        if _has_digit(match.group()):
            add(_runs(re.split(r"[-/]", match.group()), singles=True))
    for match in _DIGIT_GROUPS.finditer(folded):
        groups = [g for g in _SPLIT.split(match.group().lstrip("+")) if g]
        if sum(len(g) for g in groups) >= 6:
            add(_runs(groups, singles=False))
    for match in _EMAIL.finditer(folded):
        local, domain = match.group(1), match.group(2)
        add([local, *[p for p in re.split(r"[._+-]", local) if len(p) >= 2], *_domains(domain)])
    for match in _HOST.finditer(folded):
        add(_domains(match.group(1)))
    for match in _IPV4.finditer(folded):
        octets = match.groups()
        if all(int(o) <= 255 for o in octets):
            add([".".join(octets), ".".join(octets[:3])])
    return list(found)


def index_terms(text: str) -> str:
    """index_tokens as one space-separated string (input of to_tsvector)."""
    return " ".join(index_tokens(text))


def _joined(match: re.Match[str], *, drop_country: bool) -> str:
    value = match.group()
    groups = [g for g in _SPLIT.split(value.lstrip("+")) if g]
    if drop_country and value.startswith("+") and len(groups) > 2:
        groups = groups[1:]
    return "".join(groups)


def query_alternatives(query: str) -> tuple[str, str]:
    """Two rewritten forms of a query with every identifier joined (the second without a
    leading country code); the rest of the query is unchanged (words keep their diacritics for
    the Polish dictionary). Both equal the query when it has no identifiers; the caller OR-s
    them with the original query, so nothing the original finds is lost."""

    def rewrite(drop_country: bool) -> str:
        def code(match: re.Match[str]) -> str:
            value = match.group()
            return fold("".join(re.split(r"[-/]", value))) if _has_digit(value) else value

        def digits(match: re.Match[str]) -> str:
            value = match.group()
            groups = [g for g in _SPLIT.split(value.lstrip("+")) if g]
            if sum(len(g) for g in groups) < 6:
                return value
            return _joined(match, drop_country=drop_country)

        joined = _CODE.sub(code, _DIGIT_GROUPS.sub(digits, query))
        return _PLUS_DIGITS.sub(lambda m: m.group(1), joined)  # "+48501..." -> "48501..."

    return rewrite(False), rewrite(True)
