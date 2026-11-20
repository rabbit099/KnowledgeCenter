"""File name and headers of a download (ADR-025); the route is tested in test_lifecycle.py."""

from __future__ import annotations

from urllib.parse import unquote

import pytest

from baza_wiedzy.modules.documents.download import HEADERS, content_disposition, download_name


def parts(header: str) -> tuple[str, str]:
    kind, ascii_part, utf8_part = header.split("; ")
    assert kind == "attachment"
    assert ascii_part.startswith('filename="') and ascii_part.endswith('"')
    assert utf8_part.startswith("filename*=UTF-8''")
    return ascii_part[10:-1], unquote(utf8_part[17:])


@pytest.mark.parametrize(
    ("name", "fallback"),
    [
        ("Umowa serwisowa.pdf", "Umowa serwisowa.pdf"),
        ("Zażółć gęślą jaźń.docx", "Zazolc gesla jazn.docx"),
        ("Łódź – plan.txt", "Lodz plan.txt"),
        ('raport "Q4"; v2.pdf', "raport _Q4__ v2.pdf"),
        ("żółw.eml", "zolw.eml"),
        ("ąę.pdf", "ae.pdf"),
        ("ŹŚŁł.pdf", "ZSLl.pdf"),
        ("файл.pdf", "plik.pdf"),
        ("dane.tar.gz.txt", "dane.tar.gz.txt"),
        ("bez-rozszerzenia", "bez-rozszerzenia"),
    ],
)
def test_ascii_fallback_and_exact_utf8_name(name: str, fallback: str) -> None:
    ascii_name, exact = parts(content_disposition(name))
    assert ascii_name == fallback
    assert exact == name
    assert ascii_name.isascii() and '"' not in ascii_name and "\\" not in ascii_name


def test_header_injection_is_impossible() -> None:
    header = content_disposition('a"\r\nSet-Cookie: x=1;.pdf')
    assert "\r" not in header and "\n" not in header
    ascii_name, exact = parts(header.replace("; Set", "_Set"))  # only the exact part has '; '
    assert '"' not in ascii_name
    assert exact.startswith('a"')


@pytest.mark.parametrize(
    ("stored", "kind", "expected"),
    [
        ("umowa.docx", "docx", "umowa.docx"),
        ("UMOWA.DOCX", "docx", "UMOWA.DOCX"),
        ("umowa.doc", "docx", "umowa.doc.docx"),  # renamed file: the detected type wins
        ("notatka", "txt", "notatka.txt"),
        ("../../etc/passwd", "txt", "passwd.txt"),
        ("skan.pdf", "pdf", "skan.pdf"),
    ],
)
def test_download_name_carries_the_extension_of_the_detected_type(
    stored: str, kind: str, expected: str
) -> None:
    assert download_name(stored, kind) == expected


def test_response_headers_never_let_the_browser_render_the_file() -> None:
    assert HEADERS["x-content-type-options"] == "nosniff"
    assert "sandbox" in HEADERS["content-security-policy"]
    assert HEADERS["cache-control"] == "private, no-store"
