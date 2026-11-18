"""Child process for PDF text: `python -m baza_wiedzy.modules.ingestion.pdf_child IN OUT MAX`.

Runs pdfium (native code) outside the worker (sandbox.py). Writes a JSON file OUT:
{"page_count": n, "pages": [{"text": "...", "has_image": true}, ...]}. Exit codes (no text
on stdout or stderr, which go to files anyway): 0 ok, 3 password protected, 4 damaged,
5 more pages than MAX, 6 out of memory.
"""

from __future__ import annotations

import json
import sys

OK, ENCRYPTED, DAMAGED, TOO_MANY_PAGES, NO_MEMORY = 0, 3, 4, 5, 6


def main(source: str, target: str, max_pages: int) -> int:
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    try:
        document = pdfium.PdfDocument(source)
    except pdfium.PdfiumError as exc:
        return ENCRYPTED if "password" in str(exc).lower() else DAMAGED
    try:
        count = len(document)
        if count > max_pages:
            return TOO_MANY_PAGES
        pages = []
        for index in range(count):
            page = document[index]
            textpage = page.get_textpage()
            text = textpage.get_text_bounded()
            has_image = any(
                True for _ in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=3)
            )
            pages.append({"text": text, "has_image": has_image})
            textpage.close()
            page.close()
    except pdfium.PdfiumError:
        return DAMAGED
    finally:
        document.close()
    with open(target, "w", encoding="utf-8") as out:
        json.dump({"page_count": count, "pages": pages}, out, ensure_ascii=False)
    return OK


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1], sys.argv[2], int(sys.argv[3])))
    except MemoryError:
        sys.exit(NO_MEMORY)
