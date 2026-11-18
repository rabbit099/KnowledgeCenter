"""Split long texts into chunks of at most CHUNK_CHARS characters (CLAUDE.md 7).

PostgreSQL limits one tsvector to 1 MB; 100,000 characters stay well below it in all three
text-search configurations. Cuts fall on a paragraph break, else a line break, else a space in
the last fifth of the window; only text without any of them is cut hard.
"""

from __future__ import annotations

CHUNK_CHARS = 100_000


def split(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        floor = limit * 4 // 5
        cut = -1
        for separator in ("\n\n", "\n", " "):
            position = window.rfind(separator, floor)
            if position > 0:
                cut = position + len(separator)
                break
        if cut <= 0:
            cut = limit
        chunk = rest[:cut].strip()
        if chunk:
            chunks.append(chunk)
        rest = rest[cut:]
    rest = rest.strip()
    if rest or not chunks:
        chunks.append(rest)
    return chunks
