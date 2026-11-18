"""Extractor registry: one file per format (CLAUDE.md 6a, extension point).

A new format = a new module here with `extract(path, ctx)` and one line in REGISTRY. References
are lazy so that the web application (which imports this package only for type names) never
loads parser libraries; they are installed only in the worker image.
"""

REGISTRY: dict[str, str] = {
    "txt": "baza_wiedzy.modules.ingestion.extractors.txt:extract",
    "eml": "baza_wiedzy.modules.ingestion.extractors.eml:extract",
    "docx": "baza_wiedzy.modules.ingestion.extractors.docx:extract",
    "pdf": "baza_wiedzy.modules.ingestion.extractors.pdf:extract",
    "doc": "baza_wiedzy.modules.ingestion.extractors.doc:extract",
    "msg": "baza_wiedzy.modules.ingestion.extractors.msg:extract",
}
