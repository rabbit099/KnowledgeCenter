"""Sanitising the user's file name. It is kept only as metadata (shown, and used as the name
of a download later), never as a path."""

from __future__ import annotations

import unicodedata

MAX_LENGTH = 255
FALLBACK = "plik"


def sanitize(raw: str) -> str:
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]  # drop any directory part
    name = unicodedata.normalize("NFC", name)
    name = name.translate({ord(c): " " for c in "\t\n\r\v\f"})  # whitespace controls -> space
    # Control and format characters (including bidi overrides such as U+202E) are removed.
    name = "".join(ch for ch in name if unicodedata.category(ch) not in {"Cc", "Cf", "Cs", "Co"})
    name = " ".join(name.split())  # collapse whitespace, also tabs and newlines
    name = name.lstrip(". ")  # no hidden files, no "..", no leading spaces
    if not name:
        return FALLBACK
    if len(name) > MAX_LENGTH:
        stem, dot, extension = name.rpartition(".")
        if dot and 0 < len(extension) <= 10:
            name = stem[: MAX_LENGTH - len(extension) - 1] + "." + extension
        else:
            name = name[:MAX_LENGTH]
    return name
