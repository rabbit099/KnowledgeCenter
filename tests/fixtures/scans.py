"""Synthetic scans for the OCR tests and `make probki` (Phase 4). Nothing comes from real
documents: invented text, phone numbers from the range 600 100 xxx, an invented tax number.

Every scan is an A4 page rendered as an image at 200 dpi (DejaVu Sans, Polish and English text)
and embedded in a PDF without any text layer, like a scanner produces. Each sample has one
typical defect. Deterministic: fixed noise seed, fixed PDF creation date.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fixtures.msg import Attachment, Msg, msg_bytes
from fixtures.samples import DEJAVU, _font, eml_bytes

DPI = 200
A4 = (1654, 2339)  # pixels at 200 dpi
PDF_TYPE = "application/pdf"

BASE_LINES = [
    "Protokół odbioru urządzeń chłodniczych",
    "Zażółć gęślą jaźń. Łódź, ulica Przykładowa 7.",
    "The service report was approved by the finance department.",
    "Telefon 600 100 201, NIP 123-456-32-18",
]


def page_image(
    lines: list[str],
    *,
    font_size: int = 34,
    skew: float = 0.0,
    noise: float = 0.0,
    blur: float = 0.0,
    rotate90: bool = False,
    ink: int = 0,
    paper: int = 255,
    table: list[list[str]] | None = None,
    seed: int = 7,
) -> object:
    """A grayscale page image (PIL) with the given defects."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

    image = Image.new("L", A4, paper)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(_font(), font_size)
    y = 220
    for line in lines:
        draw.text((160, y), line, fill=ink, font=font)
        y += int(font_size * 2.2)
    if table:
        y += 40
        widths = [700, 450]
        row_height = font_size * 2
        for row in table:
            x = 160
            for width, cell in zip(widths, row, strict=False):
                draw.rectangle((x, y, x + width, y + row_height), outline=ink, width=3)
                draw.text((x + 20, y + font_size // 2), cell, fill=ink, font=font)
                x += width
            y += row_height
    if skew:
        image = image.rotate(skew, resample=Image.Resampling.BICUBIC, fillcolor=paper)
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    if noise:
        random_bytes = random.Random(seed).randbytes(A4[0] * A4[1])
        grain = Image.frombytes("L", A4, random_bytes)
        level = int(noise * 255 / 2)
        image = ImageChops.darker(image, grain.point(lambda v: 0 if v < level else 255))
        image = ImageChops.lighter(image, grain.point(lambda v: 255 if v > 255 - level else 0))
    if rotate90:
        image = image.rotate(90, expand=True, fillcolor=paper)
    return image


def _pdf() -> object:
    from fpdf import FPDF

    pdf = FPDF(unit="pt", format=(595, 842))
    pdf.set_creation_date(datetime(2026, 11, 20, tzinfo=UTC))
    return pdf


def scan_pdf(*images: object) -> bytes:
    """A PDF with one image-only page per image (no text layer)."""
    pdf = _pdf()
    for image in images:
        landscape = image.width > image.height  # type: ignore[attr-defined]
        pdf.add_page(orientation="L" if landscape else "P")  # type: ignore[attr-defined]
        width, height = (842, 595) if landscape else (595, 842)
        pdf.image(image, x=0, y=0, w=width, h=height)  # type: ignore[attr-defined]
    return bytes(pdf.output())  # type: ignore[attr-defined]


def mixed_pdf(text_lines: list[str], scan: object) -> bytes:
    """Page 1 with a real text layer, page 2 a scan."""
    pdf = _pdf()
    pdf.add_font("DejaVu", fname=DEJAVU)  # type: ignore[attr-defined]
    pdf.set_font("DejaVu", size=12)  # type: ignore[attr-defined]
    pdf.add_page()  # type: ignore[attr-defined]
    for line in text_lines:
        pdf.multi_cell(0, 16, line)  # type: ignore[attr-defined]
    pdf.add_page()  # type: ignore[attr-defined]
    pdf.image(scan, x=0, y=0, w=595, h=842)  # type: ignore[attr-defined]
    return bytes(pdf.output())  # type: ignore[attr-defined]


@dataclass(frozen=True)
class Scan:
    filename: str
    build: Callable[[], bytes]
    expect: tuple[str, ...]  # words OCR must find (case-insensitive)
    defect: str  # Polish, for OCZEKIWANE-ocr.txt


def _single(**defects: object) -> Callable[[], bytes]:
    return lambda: scan_pdf(page_image(BASE_LINES, **defects))  # type: ignore[arg-type]


def _scan_attachment_mail() -> bytes:
    return eml_bytes(
        subject="Skan protokołu – przegląd",
        plain="W załączniku skan protokołu.",
        attachments=[("protokol-skan.pdf", scan_pdf(page_image(BASE_LINES)), PDF_TYPE)],
    )


def _scan_attachment_msg() -> bytes:
    return msg_bytes(
        Msg(
            subject="Skan faktury z Outlooka",
            body="Przesyłam skan.",
            attachments=[Attachment("faktura-skan.pdf", scan_pdf(page_image(BASE_LINES)))],
        )
    )


TABLE = [["Pozycja", "Kwota"], ["Przegląd windy", "1 200 zł"], ["Wymiana filtra", "350 zł"]]

SCANS: tuple[Scan, ...] = (
    Scan("01-czysty.pdf", _single(), ("protokół", "odbioru", "approved", "600"), "czysty skan"),
    Scan("02-przekrzywiony.pdf", _single(skew=3.5), ("odbioru", "finance"),
         "przekrzywienie 3,5°"),
    Scan("03-szum.pdf", _single(noise=0.06), ("odbioru", "report"), "szum (6 % pikseli)"),
    Scan("04-rozmyty.pdf", _single(blur=1.2), ("odbioru", "department"), "rozmycie"),
    Scan("05-maly-druk.pdf", _single(font_size=22), ("odbioru", "approved"),
         "mały druk (ok. 8 pt)"),
    Scan("06-tabela.pdf", lambda: scan_pdf(page_image(["Zestawienie kosztów"], table=TABLE)),
         ("zestawienie", "windy", "filtra"), "tabela z liniami"),
    Scan("07-obrocony.pdf", _single(rotate90=True), ("odbioru", "approved"),
         "strona obrócona o 90°"),
    Scan("08-niski-kontrast.pdf", _single(ink=140, paper=200), ("odbioru", "finance"),
         "niski kontrast"),
    Scan("09-mieszany.pdf",
         lambda: mixed_pdf(["Strona pierwsza ma warstwę tekstu: umowa najmu."],
                           page_image(BASE_LINES)),
         ("najmu", "odbioru"), "strona 1 z tekstem, strona 2 skan"),
    Scan("10-mail-ze-skanem.eml", _scan_attachment_mail, ("odbioru", "approved"),
         "skan w załączniku EML"),
    Scan("11-outlook-ze-skanem.msg", _scan_attachment_msg, ("odbioru", "approved"),
         "skan w załączniku MSG"),
)  # fmt: skip


def expected_text(directory: str = "ocr") -> str:
    """OCZEKIWANE-ocr.txt for `make probki`."""
    lines = [
        "Próbki do testu OCR (syntetyczne skany, Faza 4).",
        "",
        f"Wgraj pliki z katalogu {directory}/ (np. jako bw-test). Po przetworzeniu każdy ma stan",
        "„gotowe”, na stronie dokumentu jest „Tekst: tekst z OCR” z ostrzeżeniem o numerach, a w",
        "panelu tekstu strony ze skanu mają etykietę „tekst z OCR”. Wyszukiwanie znajduje słowa",
        "ze skanów (po publikacji także jako Czytelnik).",
        "",
        "Plik                          wada                                  szukaj (przykłady)",
    ]
    for scan in SCANS:
        lines.append(f"  {scan.filename:<28} {scan.defect:<37} {', '.join(scan.expect)}")
    lines += [
        "",
        "Uwagi:",
        "  - 09-mieszany.pdf: strona 1 ma zwykły tekst (bez etykiety OCR), strona 2 z OCR.",
        "  - 10/11: skan jest w załączniku; miejsce trafienia „załącznik: … › strona 1”.",
        "  - Numery ze skanu (np. 600 100 201) mogą być rozpoznane z błędami; to opisane",
        "    ograniczenie OCR, nie błąd wyszukiwarki.",
        "",
    ]
    return "\n".join(lines)
