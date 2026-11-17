"""File type detection by content, file-name sanitising and the storage."""

from __future__ import annotations

import io
import os
import stat
import time
import uuid
import zipfile
from pathlib import Path

import pytest

from baza_wiedzy.modules.documents.filenames import sanitize
from baza_wiedzy.modules.documents.filetypes import UnsupportedFile, detect
from baza_wiedzy.modules.documents.storage import LocalStorage
from fixtures.ole2 import build

MAIL = (
    b"From: Anna Nowak <anna@example.com>\r\nTo: zespol@example.com\r\n"
    b"Subject: =?utf-8?q?Zam=C3=B3wienie?=\r\nDate: Mon, 16 Nov 2026 10:00:00 +0100\r\n"
    b"\r\nTre\xc5\x9b\xc4\x87 wiadomo\xc5\x9bci.\r\n"
)


def _docx(names: tuple[str, ...] = ("[Content_Types].xml", "word/document.xml")) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "<x/>")
    return buffer.getvalue()


SAMPLES = {
    "pdf": b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n",
    "docx": _docx(),
    "doc": build({"WordDocument": b"\xec\xa5" + b"\0" * 600, "1Table": b"\0" * 100}),
    "msg": build(
        {"__properties_version1.0": b"\0" * 32, "__substg1.0_0037001F": "Temat".encode("utf-16-le")}
    ),
    "txt": "Zażółć gęślą jaźń".encode("cp1250"),
    "eml": MAIL,
}


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / uuid.uuid4().hex
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("kind", list(SAMPLES))
def test_every_kind_is_detected_by_content(tmp_path: Path, kind: str) -> None:
    assert detect(_write(tmp_path, kind, SAMPLES[kind]), f"Plik testowy.{kind.upper()}") == kind


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("faktura.txt", SAMPLES["pdf"]),  # content does not match the extension
        ("raport.pdf", SAMPLES["txt"]),
        ("arkusz.doc", build({"Workbook": b"\0" * 600})),  # old .xls renamed to .doc
        ("prezentacja.msg", build({"PowerPoint Document": b"\0" * 600})),
        ("archiwum.docx", _docx(("[Content_Types].xml", "xl/workbook.xml"))),  # .xlsx
        ("binarny.txt", b"MZ\x90\x00\x03\x00\x00\x00"),
        ("nie-mail.eml", b"to nie jest wiadomosc e-mail\nbez naglowkow\n"),
        ("obciety.docx", SAMPLES["docx"][:40]),
        ("obciety.doc", SAMPLES["doc"][:600]),
    ],
)
def test_mismatched_or_damaged_content_is_refused(
    tmp_path: Path, filename: str, data: bytes
) -> None:
    with pytest.raises(UnsupportedFile, match="does not match its extension"):
        detect(_write(tmp_path, filename, data), filename)


@pytest.mark.parametrize("filename", ["program.exe", "obraz.png", "bez_rozszerzenia", "makro.docm"])
def test_extension_outside_the_allow_list(tmp_path: Path, filename: str) -> None:
    with pytest.raises(UnsupportedFile, match="not supported"):
        detect(_write(tmp_path, filename, SAMPLES["pdf"]), filename)


def test_empty_file(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedFile, match="empty"):
        detect(_write(tmp_path, "pusty.txt", b""), "pusty.txt")


def test_utf16_text_with_bom_is_text(tmp_path: Path) -> None:
    data = "\ufeffZażółć".encode("utf-16-le")
    assert detect(_write(tmp_path, "t.txt", data), "t.txt") == "txt"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\jan\\Pulpit\\raport roczny.pdf", "raport roczny.pdf"),
        ("..\\..\\boot.ini", "boot.ini"),
        ("faktura\u202efdp.exe", "fakturafdp.exe"),  # right-to-left override removed
        ("plik\x00\x07\nz\tsterującymi.txt", "plik z sterującymi.txt"),
        ("   ...ukryty.txt", "ukryty.txt"),
        ("Zaz\u0307o\u0301łc\u0301.docx", "Zażółć.docx"),  # NFC
        ("", "plik"),
        ("../", "plik"),
    ],
)
def test_sanitize(raw: str, expected: str) -> None:
    assert sanitize(raw) == expected


def test_sanitize_keeps_the_extension_of_long_names() -> None:
    name = sanitize("a" * 400 + ".docx")
    assert len(name) == 255 and name.endswith(".docx")


def test_storage_moves_atomically_and_makes_originals_read_only(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    incoming = storage.incoming_path()
    assert incoming.parent == tmp_path / ".incoming"
    incoming.write_bytes(b"tre\xc5\x9b\xc4\x87")
    key = storage.store(incoming)
    target = storage.original_path(key)
    assert not incoming.exists() and target.read_bytes() == b"tre\xc5\x9b\xc4\x87"
    assert target.relative_to(tmp_path).parts[:3] == ("originals", key[:2], key[2:4])
    assert not stat.S_IMODE(target.stat().st_mode) & 0o222  # nobody may write


def test_storage_key_is_never_a_user_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).original_path("../../etc/passwd")


def test_cleanup_removes_only_old_incoming_files(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    old, fresh = storage.incoming_path(), storage.incoming_path()
    old.write_bytes(b"x")
    fresh.write_bytes(b"y")
    two_hours_ago = time.time() - 7200
    os.utime(old, (two_hours_ago, two_hours_ago))
    assert storage.cleanup_incoming() == 1
    assert not old.exists() and fresh.exists()


def test_cli_storage_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from baza_wiedzy.cli import main

    storage = LocalStorage(tmp_path)
    orphan = storage.incoming_path()
    orphan.write_bytes(b"x")
    os.utime(orphan, (time.time() - 3 * 3600, time.time() - 3 * 3600))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    assert main(["storage-cleanup", "--older-than", "2"]) == 0
    assert "Removed 1 orphaned" in capsys.readouterr().out and not orphan.exists()
