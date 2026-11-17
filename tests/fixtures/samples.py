"""Synthetic test documents, generated in memory (nothing binary is committed).

All names, addresses and texts are invented; addresses use example.com (RFC 2606).
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

POLISH_TEXT = (
    "Umowa serwisowa nr 17/2026 zawarta w Łodzi pomiędzy Zakładem Usług Przykładowych "
    "a Spółdzielnią Mieszkaniową. Zażółć gęślą jaźń. Wykonawca zobowiązuje się do "
    "comiesięcznego przeglądu urządzeń chłodniczych oraz źródeł zasilania awaryjnego."
)
ENGLISH_TEXT = (
    "The service agreement covers monthly inspections of the cooling equipment and the "
    "emergency power supply. The contractor reports every failure within two working days."
)


def docx_bytes(
    paragraphs: list[str] | None = None,
    table: list[list[str]] | None = None,
    header: str | None = None,
) -> bytes:
    import docx

    document = docx.Document()
    for text in paragraphs if paragraphs is not None else [POLISH_TEXT]:
        document.add_paragraph(text)
    if table:
        grid = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                grid.cell(r, c).text = value
    if header:
        document.sections[0].header.paragraphs[0].text = header
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def replace_in_zip(data: bytes, name: str, content: bytes) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            target.writestr(item, content if item.filename == name else source.read(item))
    return out.getvalue()


def zip_bomb_docx() -> bytes:
    """A valid-looking DOCX with one entry of 50 MB of zeros (compresses about 1000:1)."""
    data = docx_bytes(["krótki tekst"])
    out = io.BytesIO(data)
    with zipfile.ZipFile(out, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/media/padding.bin", b"\0" * (50 * 1024 * 1024))
    return out.getvalue()


def xxe_docx(target: str = "file:///etc/hostname") -> bytes:
    """document.xml declaring an external entity; it must never be resolved."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
        f'<!DOCTYPE w:document [<!ENTITY xxe SYSTEM "{target}">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>przed &xxe; po</w:t></w:r></w:p></w:body></w:document>"
    ).encode()
    return replace_in_zip(docx_bytes(["zastąpione"]), "word/document.xml", xml)


def email_message(
    *,
    subject: str = "Przegląd urządzeń – październik",
    plain: str | None = POLISH_TEXT,
    html: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
    nested: list[EmailMessage] | None = None,
    date: datetime | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "Anna Przykładowa <anna@example.com>"
    message["To"] = "Biuro <biuro@example.com>, Jan Testowy <jan@example.com>"
    message["Cc"] = "archiwum@example.com"
    message["Date"] = format_datetime(date or datetime(2026, 10, 14, 9, 30, tzinfo=UTC))
    message["Message-ID"] = "<synthetic-0001@example.com>"
    if plain is not None:
        message.set_content(plain)
        if html is not None:
            message.add_alternative(html, subtype="html")
    elif html is not None:
        message.set_content(html, subtype="html")
    for name, data, content_type in attachments or []:
        maintype, subtype = content_type.split("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    for inner in nested or []:
        message.add_attachment(inner)
    return message


def eml_bytes(**kwargs: object) -> bytes:
    return email_message(**kwargs).as_bytes()  # type: ignore[arg-type]


def nested_eml(levels: int) -> bytes:
    """An e-mail containing an e-mail containing ... (`levels` attached messages deep)."""
    inner = email_message(subject=f"poziom {levels}", plain=f"Treść poziomu {levels}.")
    for level in range(levels - 1, -1, -1):
        inner = email_message(
            subject=f"poziom {level}", plain=f"Treść poziomu {level}.", nested=[inner]
        )
    return inner.as_bytes()


def damaged_headers_eml() -> bytes:
    return (
        b"From: =?utf-8?B?broken\r\n"
        b"To: <<<@@@>>>, \r\n"
        b"Subject: =?unknown-charset?Q?=C5=BC?=\r\n"
        b"Date: 32 Foo 2026 25:61:00 +9999\r\n"
        b"Message-ID: \r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n" + POLISH_TEXT.encode()
    )


# --- PDF ----------------------------------------------------------------------------------

DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"  # fonts-dejavu-core (worker image)


def text_pdf(pages: list[str] | None = None, *, image_pages: tuple[int, ...] = ()) -> bytes:
    """A PDF with a real text layer (fpdf2, DejaVu for Polish letters); pages listed in
    image_pages (1-based) contain only an image (a "scan")."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_font("DejaVu", fname=DEJAVU)
    pdf.set_font("DejaVu", size=12)
    for number, text in enumerate(pages if pages is not None else [POLISH_TEXT], start=1):
        pdf.add_page()
        if number in image_pages:
            pdf.image(_scan_png(text), x=10, y=10, w=180)
        else:
            pdf.multi_cell(0, 8, text)
    return bytes(pdf.output())


def _scan_png(text: str) -> io.BytesIO:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1200, 400), "white")
    ImageDraw.Draw(image).text((40, 160), text[:60], fill="black", font=ImageFont.load_default())
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def scanned_pdf(pages: int = 2) -> bytes:
    """Image-only PDF (no text layer), like a scanner produces."""
    numbers = range(1, pages + 1)
    return text_pdf([f"Skan strony {n}" for n in numbers], image_pages=tuple(numbers))


def encrypted_pdf(password: str = "tajne-haslo") -> bytes:
    import pikepdf

    source = pikepdf.open(io.BytesIO(text_pdf()))
    out = io.BytesIO()
    source.save(out, encryption=pikepdf.Encryption(user=password, owner=password + "-owner"))
    return out.getvalue()


def minimal_pdf(text: str = "Smoke test PDF") -> bytes:
    """A tiny valid text PDF with the standard Helvetica font (ASCII only, no libraries)."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    size = len(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref)
    return bytes(out)


# --- DOC (needs LibreOffice: test image, make probki) ------------------------------------


def doc_html(paragraphs: list[str], table: list[list[str]] | None = None) -> str:
    rows = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in table or []
    )
    body = "".join(f"<p>{text}</p>" for text in paragraphs)
    grid = f'<table border="1">{rows}</table>' if rows else ""
    return f'<html><head><meta charset="utf-8"></head><body>{body}{grid}</body></html>'


def doc_bytes(workdir: Path, paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    """Word 97 .doc made by LibreOffice from HTML (python-docx tables do not survive a DOC
    export; real Word files are not committed)."""
    import subprocess

    source = workdir / "source.html"
    source.write_text(doc_html(paragraphs, table), encoding="utf-8")
    subprocess.run(
        ["soffice", "--headless", f"-env:UserInstallation={(workdir / 'lo-profile').as_uri()}",
         "--convert-to", "doc:MS Word 97", "--outdir", str(workdir), str(source)],
        check=True, capture_output=True, timeout=120,
    )  # fmt: skip
    return (workdir / "source.doc").read_bytes()
