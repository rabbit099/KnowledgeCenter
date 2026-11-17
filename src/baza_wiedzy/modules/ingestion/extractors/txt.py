"""Plain text: encoding detection limited to UTF-8, Windows-1250 and ISO-8859-2.

Rule (ADR-022, docs: ingestion-and-ocr.md "Text encodings"):
1. A byte order mark decides (UTF-8, UTF-16 LE/BE).
2. Bytes that are valid UTF-8 are UTF-8 (pure ASCII included).
3. Otherwise Windows-1250 and ISO-8859-2 are tried. Bytes 0x80-0x9F are control characters in
   ISO-8859-2, so their presence rules it out. The candidate that yields more Polish letters
   wins; a tie goes to Windows-1250 (the usual encoding of Polish Windows files).
4. Confidence check: at least MIN_PLAUSIBLE of the non-ASCII characters of the chosen decoding
   must be Polish letters or common typographic signs. Below that the file fails with a clear
   message instead of a guess (a different code page such as CP852, or not text at all).
"""

from __future__ import annotations

import codecs
from pathlib import Path

from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.modules.ingestion.extractors.base import Context, PermanentError

POLISH = frozenset("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
# Typographic quotes and dashes, ellipsis, bullets, degree, section, euro, no-break space, ...
TYPOGRAPHY = frozenset(
    "\u201e\u201d\u201c\u2018\u2019\u00ab\u00bb\u2013\u2014\u2026\u2022\u00b7\u00b0\u00a7"
    "\u00a9\u00ae\u20ac\u00a0\u00d7\u00f7\u00b1\u00b9\u00b2\u00b3\u00bc\u00bd\u00be\u00b5\u00b6"
)
MIN_PLAUSIBLE = 0.8
UNKNOWN_ENCODING = N_(
    "The text encoding could not be recognised (supported: UTF-8, Windows-1250, ISO-8859-2). "
    "Save the file as UTF-8 and upload it again."
)

BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig", "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16", "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16", "utf-16"),
)
LEGACY = (("cp1250", "windows-1250"), ("iso8859_2", "iso-8859-2"))


def _plausible(text: str) -> float:
    non_ascii = [ch for ch in text if ord(ch) > 127]
    if not non_ascii:
        return 1.0
    good = sum(1 for ch in non_ascii if ch in POLISH or ch in TYPOGRAPHY)
    return good / len(non_ascii)


def decode(data: bytes) -> tuple[str, str]:
    """Return (text, encoding label) or raise PermanentError."""
    for bom, codec, label in BOMS:
        if data.startswith(bom):
            try:
                return data.decode(codec), label
            except UnicodeDecodeError:
                raise PermanentError(UNKNOWN_ENCODING, "encoding") from None
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    has_c1 = any(0x80 <= byte <= 0x9F for byte in data)
    candidates: list[tuple[int, int, str, str]] = []
    for order, (codec, label) in enumerate(LEGACY):
        if codec == "iso8859_2" and has_c1:
            continue
        try:
            text = data.decode(codec)
        except UnicodeDecodeError:
            continue
        polish = sum(1 for ch in text if ch in POLISH)
        candidates.append((-polish, order, text, label))
    if not candidates:
        raise PermanentError(UNKNOWN_ENCODING, "encoding")
    _, _, text, label = min(candidates)
    if _plausible(text) < MIN_PLAUSIBLE:
        raise PermanentError(UNKNOWN_ENCODING, "encoding")
    return text, label


def extract(path: Path, ctx: Context) -> None:
    text, label = decode(path.read_bytes())
    ctx.note("encoding", value=label)
    ctx.add_text(text)
