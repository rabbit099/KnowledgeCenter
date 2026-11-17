"""Stage C extractors and limits: PDF, DOC (LibreOffice), MSG, RTF, XML bombs, isolation.

Everything is generated in the tests. DOC tests need LibreOffice (`soffice`), present in the
test image used by `make test-db`; elsewhere they are skipped.
"""

from __future__ import annotations

import io
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

from baza_wiedzy.modules.documents.filetypes import UnsupportedFile, detect
from baza_wiedzy.modules.ingestion import sandbox
from baza_wiedzy.modules.ingestion.extractors.base import (
    Collector,
    Context,
    LimitExceeded,
    Limits,
    PermanentError,
    find,
)
from baza_wiedzy.modules.ingestion.extractors.rtf_text import rtf_to_text
from baza_wiedzy.modules.ingestion.extractors.txt import decode
from fixtures.msg import Attachment, Msg, msg_bytes
from fixtures.ole2 import build
from fixtures.samples import (
    ENGLISH_TEXT,
    POLISH_TEXT,
    doc_bytes,
    docx_bytes,
    eml_bytes,
    encrypted_pdf,
    minimal_pdf,
    replace_in_zip,
    scanned_pdf,
    text_pdf,
)

_no_soffice = pytest.mark.skipif(
    shutil.which("soffice") is None, reason="LibreOffice not installed (runs in make test-db)"
)


def needs_soffice(test: Any) -> Any:
    return pytest.mark.tools(_no_soffice(test))


DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PLAIN_RTF = (
    rb"{\rtf1\ansi\ansicpg1250\deff0{\fonttbl{\f0 Arial;}}{\colortbl;\red0\green0\blue0;}"
    rb"{\*\generator Test;}\f0\fs20 Tre\'9c\'e6 tylko w RTF: za\'bf\'f3\'b3\'e6 g\'ea\'9cl\'b9 "
    rb"ja\'9f\'f1.\par Druga linia \u321?\u243?d\u378?.\par\trowd\cellx1000 A\cell B\cell\row}"
)
HTML_RTF = (
    rb"{\rtf1\ansi\ansicpg1252\fromhtml1 \deff0{\fonttbl{\f0\fswiss Arial;}}"
    rb"{\*\htmltag19 <html>}{\*\htmltag34 <body>}{\*\htmltag64 <p>}HTML w RTF"
    rb"{\*\htmltag72 </p>}{\*\htmltag58 </body>}{\*\htmltag27 </html>}}"
)


def run(tmp_path: Path, kind: str, name: str, data: bytes, **limits: int) -> Collector:
    source = tmp_path / name
    source.write_bytes(data)
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    collector = Collector(limits=Limits(**limits), workdir=workdir)
    extractor = find(kind)
    assert extractor is not None
    extractor(source, Context(collector=collector))
    return collector


# --- TXT (decision 2) ---------------------------------------------------------------------

LONG_PL = (
    "Zażółć gęślą jaźń. Ćma ścięła źdźbło; żółw ćwiczy łąkę. Pośpiech: świeże źródło, "
    "ożywczy żar, ślęczeć nad księgą. Ąę ĆĘŁ ŃÓŚ ŹŻ ąęćśźż. " * 20
)


@pytest.mark.parametrize(
    ("bom", "codec", "label"),
    [
        (b"\xef\xbb\xbf", "utf-8", "utf-8"),
        (b"\xff\xfe", "utf-16-le", "utf-16"),  # Windows Notepad "Unicode"
        (b"\xfe\xff", "utf-16-be", "utf-16"),  # "Unicode big endian"
    ],
)
def test_byte_order_marks(bom: bytes, codec: str, label: str) -> None:
    assert decode(bom + LONG_PL.encode(codec)) == (LONG_PL, label)


@pytest.mark.parametrize(
    ("codec", "label"), [("cp1250", "windows-1250"), ("iso8859_2", "iso-8859-2")]
)
def test_long_text_in_windows_1250_and_iso_8859_2(codec: str, label: str) -> None:
    assert decode(LONG_PL.encode(codec)) == (LONG_PL, label)


def test_charset_normalizer_is_not_used() -> None:
    root = Path(__file__).resolve().parents[2] / "src"
    assert not [p for p in root.rglob("*.py") if "charset_normalizer" in p.read_text()]


# --- PDF ----------------------------------------------------------------------------------


def test_pdf_text_per_page(tmp_path: Path) -> None:
    result = run(tmp_path, "pdf", "a.pdf", text_pdf([POLISH_TEXT, ENGLISH_TEXT]))
    assert result.page_count == 2
    assert [(p.page_no, p.part_type, p.needs_ocr) for p in result.parts] == [
        (1, "page", False),
        (2, "page", False),
    ]
    assert " ".join(result.parts[0].text.split()) == POLISH_TEXT


def test_scanned_pages_are_marked_for_ocr(tmp_path: Path) -> None:
    result = run(tmp_path, "pdf", "scan.pdf", scanned_pdf(2))
    assert [(p.page_no, p.needs_ocr, p.text) for p in result.parts] == [
        (1, True, ""),
        (2, True, ""),
    ]
    mixed = run(tmp_path, "pdf", "mixed.pdf", text_pdf([POLISH_TEXT, "x"], image_pages=(2,)))
    assert [(p.page_no, p.needs_ocr) for p in mixed.parts] == [(1, False), (2, True)]


def test_blank_pages_are_skipped(tmp_path: Path) -> None:
    result = run(tmp_path, "pdf", "a.pdf", text_pdf([POLISH_TEXT, " "]))
    assert result.page_count == 2 and [p.page_no for p in result.parts] == [1]


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (encrypted_pdf(), "pdf_encrypted"),
        (b"%PDF-1.4\n%garbage without objects\n", "pdf_damaged"),
        (minimal_pdf()[:120], "pdf_damaged"),
    ],
)
def test_encrypted_and_damaged_pdfs_fail_permanently(
    tmp_path: Path, data: bytes, code: str
) -> None:
    with pytest.raises(PermanentError) as error:
        run(tmp_path, "pdf", "a.pdf", data)
    assert error.value.code == code
    assert "PDF" in error.value.message or "damaged" in error.value.message


def test_page_limit(tmp_path: Path) -> None:
    with pytest.raises(LimitExceeded):
        run(tmp_path, "pdf", "a.pdf", text_pdf(["1", "2", "3"]), max_pages=2)


# --- isolation ----------------------------------------------------------------------------


def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "grandchild.pid"
    script = f"sleep 60 & echo $! > {marker}; wait"
    started = time.monotonic()
    with pytest.raises(sandbox.IsolationTimeout):
        sandbox.run(["sh", "-c", script], workdir=tmp_path, timeout=1)
    assert time.monotonic() - started < 10
    grandchild = int(marker.read_text())
    time.sleep(0.2)
    assert (
        not Path(f"/proc/{grandchild}").exists()
        or "Z" in Path(f"/proc/{grandchild}/stat").read_text().split()[2]
    )


def test_memory_limit_stops_the_child_not_the_worker(tmp_path: Path) -> None:
    finished = sandbox.run(
        [sys.executable, "-c", "x = bytearray(600 * 1024 * 1024)"],
        workdir=tmp_path,
        timeout=30,
        memory_mb=200,
    )
    assert finished.returncode != 0


def test_child_gets_a_minimal_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:3128")
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret@db/x")
    sandbox.run(["sh", "-c", "env > env.txt"], workdir=tmp_path, timeout=10)
    env = (tmp_path / "env.txt").read_text()
    assert "PROXY" not in env and "DATABASE_URL" not in env
    assert f"HOME={tmp_path / 'home'}" in env


# --- DOC ----------------------------------------------------------------------------------


def test_encrypted_doc_is_refused_before_libreoffice(tmp_path: Path) -> None:
    fib = bytearray(64)
    fib[0:2] = (0xA5EC).to_bytes(2, "little")
    fib[10:12] = (0x0100).to_bytes(2, "little")  # fEncrypted
    with pytest.raises(PermanentError) as error:
        run(
            tmp_path,
            "doc",
            "a.doc",
            build({"WordDocument": bytes(fib) * 10, "1Table": b"\0" * 600}),
        )
    assert error.value.code == "doc_encrypted" and "password" in error.value.message


@needs_soffice
def test_doc_through_libreoffice(tmp_path: Path) -> None:
    doc = doc_bytes(tmp_path, ["Akapit z polskimi znakami: ąęśćźż."], table=[["A", "B"]])
    assert detect(_write(tmp_path / "x.doc", doc), "x.doc") == "doc"
    result = run(tmp_path, "doc", "a.doc", doc)
    text = result.parts[0].text
    assert "Akapit z polskimi znakami: ąęśćźż." in text and "A | B" in text
    profiles = list((tmp_path / "work").glob("doc-*/profile"))
    assert profiles  # a private profile per conversion, inside the attempt's directory
    assert not _running("soffice")  # the whole process group was killed after conversion


@needs_soffice
def test_damaged_doc_fails_permanently(tmp_path: Path) -> None:
    with pytest.raises(PermanentError):
        run(tmp_path, "doc", "a.doc", build({"WordDocument": b"\x00" * 4096}))


@needs_soffice
def test_libreoffice_timeout_is_transient(tmp_path: Path) -> None:
    doc = doc_bytes(tmp_path, ["tekst"])
    with pytest.raises(sandbox.IsolationTimeout):
        run(tmp_path, "doc", "a.doc", doc, libreoffice_timeout_seconds=0)


def _running(name: str) -> list[str]:
    found = []
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if name.encode() in cmdline.read_bytes():
                found.append(cmdline.parent.name)
        except OSError:
            continue
    return found


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


# --- MSG ----------------------------------------------------------------------------------


def test_msg_headers_body_and_attachments(tmp_path: Path) -> None:
    inner = Msg(
        subject="Wiadomość przekazana",
        body="Treść przekazanej wiadomości.",
        attachments=[Attachment("w-srodku.txt", "Plik w środku".encode())],
    )
    message = Msg(
        attachments=[
            Attachment("umowa.docx", docx_bytes([ENGLISH_TEXT])),
            Attachment("skan.pdf", text_pdf([POLISH_TEXT])),
            Attachment("logo.png", b"\x89PNG\r\n\x1a\n"),
            Attachment("przekazana.msg", message=inner),
        ]
    )
    result = run(tmp_path, "msg", "a.msg", msg_bytes(message))
    texts = [(p.origin, p.text[:30]) for p in result.parts]
    assert texts[0] == ([], "Treść wiadomości: zażółć gęślą")
    assert ([{"type": "attachment", "name": "umowa.docx"}], ENGLISH_TEXT[:30]) in texts
    assert any(o == [{"type": "attachment", "name": "skan.pdf"}] for o, _ in texts)
    forwarded = [{"type": "attachment", "name": "przekazana.msg"}]
    assert (forwarded, "Treść przekazanej wiadomości.") in texts
    assert ([*forwarded, {"type": "attachment", "name": "w-srodku.txt"}], "Plik w środku") in texts
    assert {"code": "skipped", "origin": [{"type": "attachment", "name": "logo.png"}],
            "reason": "unsupported"} in result.notes  # fmt: skip
    email = result.email
    assert email is not None and email.subject == "Przegląd urządzeń – październik"
    assert email.from_addr is not None and "anna@example.com" in email.from_addr
    assert email.to_addrs is not None and "jan@example.com" in email.to_addrs
    assert email.cc_addrs is not None and "archiwum@example.com" in email.cc_addrs
    assert email.sent_at is not None and email.sent_at.year == 2026
    assert email.message_id == "<synthetic-msg-0001@example.com>"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (Msg(body=None, html="<p>Tylko <b>HTML</b></p><script>zly()</script>"), "Tylko HTML"),
        (Msg(body=None, rtf=PLAIN_RTF), "Treść tylko w RTF: zażółć gęślą jaźń.\nDruga linia Łódź."),
        (Msg(body=None, rtf=HTML_RTF), "HTML w RTF"),
    ],
)
def test_msg_html_and_rtf_bodies(tmp_path: Path, message: Msg, expected: str) -> None:
    result = run(tmp_path, "msg", "a.msg", msg_bytes(message))
    assert result.parts[0].text.startswith(expected)


def test_nested_msgs_stop_at_the_depth_limit(tmp_path: Path) -> None:
    message = Msg(subject="poziom 4", body="Poziom 4.")
    for level in range(3, -1, -1):
        message = Msg(
            subject=f"poziom {level}",
            body=f"Poziom {level}.",
            attachments=[Attachment(f"poziom-{level + 1}.msg", message=message)],
        )
    result = run(tmp_path, "msg", "a.msg", msg_bytes(message), max_depth=3)
    assert [p.text for p in result.parts] == ["Poziom 0.", "Poziom 1.", "Poziom 2.", "Poziom 3."]
    assert [n["reason"] for n in result.notes] == ["too_deep"]


def test_msg_attached_to_an_eml(tmp_path: Path) -> None:
    data = eml_bytes(attachments=[("notatka.msg", msg_bytes(), "application/vnd.ms-outlook")])
    result = run(tmp_path, "eml", "a.eml", data)
    assert [p.origin for p in result.parts][1] == [{"type": "attachment", "name": "notatka.msg"}]
    assert result.parts[1].text == "Treść wiadomości: zażółć gęślą jaźń."
    # The e-mail's own headers are the metadata, not those of the attached message.
    assert result.email is not None and result.email.message_id == "<synthetic-0001@example.com>"


def test_damaged_msg_fails_permanently(tmp_path: Path) -> None:
    with pytest.raises(PermanentError):
        run(tmp_path, "msg", "a.msg", build({"__properties_version1.0": b"\xff" * 10}))


def test_rtf_to_text() -> None:
    assert rtf_to_text(PLAIN_RTF) == (
        "Treść tylko w RTF: zażółć gęślą jaźń.\nDruga linia Łódź.\nA | B |"
    )
    assert rtf_to_text(rb"{\rtf1{\*\unknown secret}{\pict 0102}visible\~text}") == "visible text"


# --- XML and archive bombs, encrypted Office --------------------------------------------


def _document_xml(body: str, doctype: str = "") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + doctype +
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    ).encode()  # fmt: skip


def test_billion_laughs_is_not_expanded(tmp_path: Path) -> None:
    entities = '<!ENTITY a0 "hahahahahahahahahaha">' + "".join(
        f'<!ENTITY a{n} "{f"&a{n - 1};" * 10}">' for n in range(1, 10)
    )
    xml = _document_xml(
        "<w:p><w:r><w:t>przed &a9; po</w:t></w:r></w:p>", f"<!DOCTYPE w:document [{entities}]>"
    )
    started = time.monotonic()
    try:
        result = run(
            tmp_path, "docx", "a.docx", replace_in_zip(docx_bytes(["x"]), "word/document.xml", xml)
        )
        assert sum(len(p.text) for p in result.parts) < 1000
    except PermanentError:
        pass  # refusing the file is fine too; expanding it is not
    assert time.monotonic() - started < 10


def test_deeply_nested_xml_fails_cleanly(tmp_path: Path) -> None:
    body = "<w:p><w:r><w:t>głęboko</w:t></w:r></w:p>"
    nested = "<w:sdt><w:sdtContent>" * 5000 + body + "</w:sdtContent></w:sdt>" * 5000
    data = replace_in_zip(docx_bytes(["x"]), "word/document.xml", _document_xml(nested))
    started = time.monotonic()
    with pytest.raises(PermanentError):
        run(tmp_path, "docx", "a.docx", data)
    assert time.monotonic() - started < 10


def test_many_small_zip_entries_are_refused(tmp_path: Path) -> None:
    out = io.BytesIO(docx_bytes(["x"]))
    with zipfile.ZipFile(out, "a") as archive:
        for number in range(5001):
            archive.writestr(f"word/media/x{number}.bin", b"")
    with pytest.raises(PermanentError) as error:
        run(tmp_path, "docx", "a.docx", out.getvalue())
    assert error.value.code == "zip_entries"


def test_encrypted_docx_is_refused_at_upload_with_a_clear_message(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "a.docx",
        build({"EncryptionInfo": b"\x04\x00" * 100, "EncryptedPackage": b"\0" * 5000}),
    )
    with pytest.raises(UnsupportedFile) as error:
        detect(path, "tajny.docx")
    assert "password" in error.value.message


def test_encrypted_attachment_is_skipped_with_a_note(tmp_path: Path) -> None:
    secret = build({"EncryptionInfo": b"\x04\x00" * 100, "EncryptedPackage": b"\0" * 5000})
    data = eml_bytes(
        attachments=[
            ("tajny.docx", secret, DOCX_TYPE),
            ("tajny.pdf", encrypted_pdf(), "application/pdf"),
        ]
    )
    result = run(tmp_path, "eml", "a.eml", data)
    assert [n["reason"] for n in result.notes] == ["encrypted", "encrypted"]
    assert len(result.parts) == 1


@needs_soffice
def test_large_repetitive_doc_is_not_mistaken_for_an_archive_bomb(tmp_path: Path) -> None:
    doc = doc_bytes(tmp_path, [POLISH_TEXT] * 3000)
    result = run(tmp_path, "doc", "a.doc", doc)
    assert sum(len(p.text) for p in result.parts) > 500_000


def test_eml_and_msg_headers_have_the_same_format(tmp_path: Path) -> None:
    eml = run(tmp_path, "eml", "a.eml", eml_bytes()).email
    message = Msg(
        to=[("Biuro", "biuro@example.com"), ("Jan Testowy", "jan@example.com")],
        cc=[("", "archiwum@example.com")],
    )
    msg = run(tmp_path, "msg", "a.msg", msg_bytes(message)).email
    assert eml is not None and msg is not None
    expected = (
        "Anna Przykładowa <anna@example.com>",
        "Biuro <biuro@example.com>, Jan Testowy <jan@example.com>",
        "archiwum@example.com",
    )
    assert (eml.from_addr, eml.to_addrs, eml.cc_addrs) == expected
    assert (msg.from_addr, msg.to_addrs, msg.cc_addrs) == expected


def test_nested_origin_names_the_word_attachment_once() -> None:
    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.core.web.templating import make_templates

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    env = make_templates(settings, ("baza_wiedzy.modules.ingestion",)).env
    render = env.from_string(
        '{% from "ingestion/_macros.html" import origin_path %}{{ origin_path(o) }}'
    ).render
    nested = [
        {"type": "attachment", "name": "a.msg"},
        {"type": "attachment", "name": "b.docx"},
        {"type": "page", "no": 2},
    ]
    assert render(o=nested) == "załącznik: a.msg › b.docx › strona 2"
    assert render(o=[]) == "treść"
