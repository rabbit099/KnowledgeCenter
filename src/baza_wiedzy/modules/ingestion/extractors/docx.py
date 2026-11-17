"""Word 2007+ (.docx): python-docx with a raw-XML fallback.

A DOCX is a ZIP archive, so the archive is checked before anything is decompressed (zip-bomb
limits: number of entries, total uncompressed size, compression ratio of large entries).
XML is parsed without DTDs, external entities or network access (python-docx's own parser
does not resolve entities; the fallback uses an explicitly hardened lxml parser).

Text order: body paragraphs and tables as they appear (table rows as "cell | cell"), then
headers and footers of each section.
"""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.modules.ingestion.extractors.base import DAMAGED, Context, PermanentError

log = logging.getLogger(__name__)

MAX_ENTRIES = 5000
MAX_UNCOMPRESSED = 200 * 1024 * 1024
MAX_RATIO = 100
RATIO_FROM = 1024 * 1024  # small entries compress well legitimately
MAX_XML = 100 * 1024 * 1024
ARCHIVE_LIMITS = N_("The file unpacks to much more data than allowed (possible archive bomb).")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def check_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise PermanentError(DAMAGED, "zip") from exc
    if len(entries) > MAX_ENTRIES:
        raise PermanentError(ARCHIVE_LIMITS, "zip_entries")
    if sum(entry.file_size for entry in entries) > MAX_UNCOMPRESSED:
        raise PermanentError(ARCHIVE_LIMITS, "zip_size")
    for entry in entries:
        if entry.file_size > RATIO_FROM and entry.file_size > MAX_RATIO * max(
            entry.compress_size, 1
        ):
            raise PermanentError(ARCHIVE_LIMITS, "zip_ratio")


def _table_lines(table: Any) -> Iterator[str]:
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            text = " ".join(cell.text.split())
            if not cells or cells[-1] != text:  # merged cells repeat their text
                cells.append(text)
        line = " | ".join(cells).strip(" |")
        if line:
            yield line


def _with_python_docx(path: Path) -> list[str]:
    import docx
    from docx.table import Table

    document = docx.Document(str(path))
    lines: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            lines.extend(_table_lines(block))
        else:
            lines.append(block.text)
    for section in document.sections:
        for part in (section.header, section.footer):
            if part.is_linked_to_previous:
                continue
            lines.extend(p.text for p in part.paragraphs)
            for table in part.tables:
                lines.extend(_table_lines(table))
    return lines


def _with_raw_xml(path: Path) -> list[str]:
    from lxml import etree

    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("word/document.xml")
        if info.file_size > MAX_XML:
            raise PermanentError(ARCHIVE_LIMITS, "xml_size")
        root = etree.fromstring(archive.read(info), parser)
    lines: list[str] = []
    for paragraph in root.iter(f"{W}p"):
        lines.append("".join(node.text or "" for node in paragraph.iter(f"{W}t")))
    return lines


def extract(path: Path, ctx: Context) -> None:
    check_archive(path)
    try:
        lines = _with_python_docx(path)
    except PermanentError:
        raise
    except Exception as exc:
        log.info("python-docx could not read the file (%s); raw XML fallback", type(exc).__name__)
        try:
            lines = _with_raw_xml(path)
        except PermanentError:
            raise
        except Exception as fallback_exc:
            raise PermanentError(DAMAGED, "docx") from fallback_exc
        ctx.note("fallback", value="raw_xml")
    ctx.add_text("\n".join(lines))
