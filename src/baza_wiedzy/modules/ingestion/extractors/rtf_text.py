"""Plain RTF to text, for e-mail bodies stored only as RTF (MSG without plain or HTML body).

Small tokenizer, no dependency: paragraphs and line breaks become new lines, table cells
" | ", `\\'hh` bytes are decoded with the document's code page (`\\ansicpg`, default
Windows-1250 for Polish mail), `\\uN` gives Unicode characters (skipping the `\\ucN`
fallback characters), and non-text destinations (font and colour tables, styles, pictures,
embedded objects, `{\\*...}` groups) are dropped. Nothing is executed; the input size is
already bounded by the upload limit.
"""

from __future__ import annotations

import re

SKIP_DESTINATIONS = frozenset(
    {
        "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "themedata",
        "colorschememapping", "datastore", "latentstyles", "listtable", "listoverridetable",
        "rsidtbl", "generator", "xmlnstbl", "mmathPr", "pgdsctbl", "filetbl", "revtbl",
        "fldinst", "bkmkstart", "bkmkend", "pntext", "pntxta", "pntxtb", "nonshppict",
    }
)  # fmt: skip
NEWLINE_WORDS = frozenset({"par", "line", "row", "sect", "page"})
TOKEN = re.compile(
    rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([^\\{}\r\n]+)|[\r\n]+",
    re.DOTALL,
)


def rtf_to_text(data: bytes) -> str:
    out: list[str] = []
    stack: list[tuple[bool, int]] = []
    skip = False
    uc = 1  # characters to skip after \uN
    pending_skip = 0
    codepage = "cp1250"
    pending_bytes = bytearray()
    group_start = False

    def flush() -> None:
        if pending_bytes:
            out.append(pending_bytes.decode(codepage, errors="replace"))
            pending_bytes.clear()

    for match in TOKEN.finditer(data):
        word, arg, hex_byte, symbol, brace, text = match.groups()
        if brace == b"{":
            flush()
            stack.append((skip, uc))
            group_start = True
            continue
        if brace == b"}":
            flush()
            if stack:
                skip, uc = stack.pop()
            group_start = False
            continue
        starts_group, group_start = group_start, False
        if word is not None:
            name = word.decode("ascii")
            if starts_group and name in SKIP_DESTINATIONS:
                skip = True
            if skip:
                continue
            if name == "ansicpg" and arg:
                flush()
                codepage = f"cp{int(arg)}"
                try:
                    "".encode(codepage)
                except LookupError:
                    codepage = "cp1250"
            elif name == "uc" and arg:
                uc = int(arg)
            elif name == "u" and arg:
                flush()
                value = int(arg)
                out.append(chr(value + 65536 if value < 0 else value))
                pending_skip = uc
            elif name in NEWLINE_WORDS:
                flush()
                out.append("\n")
            elif name == "tab":
                flush()
                out.append("\t")
            elif name == "cell":
                flush()
                out.append(" | ")
            continue
        if symbol is not None:
            if symbol == b"*" and starts_group:
                skip = True
                continue
            if skip:
                continue
            if pending_skip:
                pending_skip -= 1
                continue
            if symbol in (b"\\", b"{", b"}"):
                pending_bytes.extend(symbol)
            elif symbol == b"~":
                flush()
                out.append("\u00a0")
            continue
        if skip:
            continue
        if hex_byte is not None:
            if pending_skip:
                pending_skip -= 1
                continue
            pending_bytes.append(int(hex_byte, 16))
            continue
        if text is not None:
            if pending_skip:
                drop = min(pending_skip, len(text))
                text = text[drop:]
                pending_skip -= drop
            pending_bytes.extend(text)
    flush()
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip(" ") for line in "".join(out).split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
