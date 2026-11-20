"""Synthetic sample files for a manual test of upload and processing: `make probki DIR=...`.

Runs in the test image (LibreOffice for the DOC file). Every file is generated here from
invented text with example.com addresses; nothing comes from real documents or mail.
OCZEKIWANE.txt in the target directory lists the expected result of each file.
The subdirectory wyszukiwanie/ holds the search corpus (tests/fixtures/search_corpus.py, the
same files the golden search tests use) and OCZEKIWANE-wyszukiwanie.txt with the queries.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fixtures import search_corpus
from fixtures.msg import Attachment, Msg, msg_bytes
from fixtures.samples import (
    ENGLISH_TEXT,
    POLISH_TEXT,
    doc_bytes,
    docx_bytes,
    eml_bytes,
    encrypted_pdf,
    scanned_pdf,
    text_pdf,
)

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TABLE = [["Pozycja", "Kwota"], ["Przegląd urządzeń", "1 200,00 zł"]]

EXPECTED = """Próbki do ręcznego testu (syntetyczne). Oczekiwany wynik po wgraniu:

01-notatka-cp1250.txt       gotowe; 1 część, polski, adnotacja „kodowanie tekstu windows-1250”
02-dos-cp852.txt            błąd: „Nie rozpoznano kodowania tekstu…”, 1 próba
03-umowa.docx               gotowe; tekst z tabelą „Pozycja | Kwota”, nagłówek strony
04-stary-word.doc           gotowe (LibreOffice); tekst z tabelą „Pozycja | Kwota”
05-raport.pdf               gotowe; 2 części „strona 1” (polski) i „strona 2” (angielski)
06-skan.pdf                 wymaga OCR; 2 strony oznaczone do OCR (Faza 4)
07-zaszyfrowany.pdf         błąd: „PDF jest zabezpieczony hasłem…”
08-mail-z-zalacznikami.eml  gotowe; tytuł = temat; treść + załączniki: raport.pdf (strony),
                            umowa.docx, notatka.msg; logo.png pominięte z adnotacją
09-outlook.msg              gotowe; tytuł = temat; nadawca, odbiorcy, DW, data; załącznik
                            umowa.docx i załączona wiadomość z własnym załącznikiem
10-uszkodzony.pdf           błąd: „Plik jest uszkodzony…”
11-uszkodzony.docx          odrzucony przy wgrywaniu (zawartość nie pasuje do rozszerzenia)
"""


def main(target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp())
    memo = Msg(
        subject="Notatka z przeglądu",
        body="Notatka z Outlooka: łódź, żółw, źdźbło.",
        attachments=[Attachment("umowa.docx", docx_bytes([ENGLISH_TEXT]))],
    )
    files = {
        "01-notatka-cp1250.txt": POLISH_TEXT.encode("cp1250"),
        "02-dos-cp852.txt": "Zażółć gęślą jaźń. Łódź, Śląsk.".encode("cp852"),
        "03-umowa.docx": docx_bytes(
            [POLISH_TEXT, "Drugi akapit: ąęśćźżłńó."], table=TABLE, header="Spółdzielnia"
        ),
        "04-stary-word.doc": doc_bytes(work, ["Dokument Word 97: zażółć gęślą jaźń."], TABLE),
        "05-raport.pdf": text_pdf([POLISH_TEXT, ENGLISH_TEXT]),
        "06-skan.pdf": scanned_pdf(2),
        "07-zaszyfrowany.pdf": encrypted_pdf(),
        "08-mail-z-zalacznikami.eml": eml_bytes(
            subject="Przegląd urządzeń – materiały",
            html="<p>Wersja HTML</p>",
            attachments=[
                ("raport.pdf", text_pdf([POLISH_TEXT, ENGLISH_TEXT]), "application/pdf"),
                ("umowa.docx", docx_bytes([ENGLISH_TEXT]), DOCX_TYPE),
                ("notatka.msg", msg_bytes(memo), "application/vnd.ms-outlook"),
                ("logo.png", b"\x89PNG\r\n\x1a\n", "image/png"),
            ],
        ),
        "09-outlook.msg": msg_bytes(
            Msg(
                subject="Zebranie zarządu – protokół",
                attachments=[
                    Attachment("umowa.docx", docx_bytes([POLISH_TEXT], table=TABLE)),
                    Attachment("przekazana.msg", message=memo),
                ],
            )
        ),
        "10-uszkodzony.pdf": text_pdf()[:400],
        "11-uszkodzony.docx": b"PK\x03\x04" + b"\x00" * 300,
    }
    for name, data in files.items():
        (target / name).write_bytes(data)
    (target / "OCZEKIWANE.txt").write_text(EXPECTED, encoding="utf-8")
    corpus = target / "wyszukiwanie"
    corpus.mkdir(exist_ok=True)
    for doc in search_corpus.DOCS:
        (corpus / doc.filename).write_bytes(doc.build())
    (target / "OCZEKIWANE-wyszukiwanie.txt").write_text(
        search_corpus.expected_text("wyszukiwanie"), encoding="utf-8"
    )
    print(
        f"{len(files)} sample files written to {target}, "
        f"{len(search_corpus.DOCS)} search samples to {corpus}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "probki")))
