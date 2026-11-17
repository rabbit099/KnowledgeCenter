"""Language of a text part: Polish, English or unknown (lingua, limited to pl and en).

The result is informational and a filter; it never selects the search index (CLAUDE.md 7).
Texts with fewer letters than LANGUAGE_MIN_LETTERS are "unknown" rather than a guess. Long
texts are judged by their beginning (SAMPLE characters), which is enough and keeps it fast.
The detector loads only the two language models (about 30 MB) once per worker process.
"""

from __future__ import annotations

from functools import cache
from typing import Any

SAMPLE = 20_000
UNKNOWN = "unknown"


@cache
def _detector() -> Any:
    from lingua import Language, LanguageDetectorBuilder

    return (
        LanguageDetectorBuilder.from_languages(Language.POLISH, Language.ENGLISH)
        .with_minimum_relative_distance(0.1)
        .build()
    )


def _enough_letters(text: str, minimum: int) -> bool:
    count = 0
    for ch in text:
        if ch.isalpha():
            count += 1
            if count >= minimum:
                return True
    return False


def detect(text: str, *, min_letters: int) -> str:
    sample = text[:SAMPLE]
    if not _enough_letters(sample, max(1, min_letters)):
        return UNKNOWN
    from lingua import Language

    language = _detector().detect_language_of(sample)
    if language == Language.POLISH:
        return "pl"
    if language == Language.ENGLISH:
        return "en"
    return UNKNOWN
