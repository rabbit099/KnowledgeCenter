"""Synthetic test documents, generated in memory (nothing binary is committed).

All names, addresses and texts are invented; addresses use example.com (RFC 2606).
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime

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
