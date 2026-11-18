"""Extractors of the ingestion module on synthetic files: TXT, EML, DOCX, HTML, chunks, language."""

from __future__ import annotations

from pathlib import Path

import pytest

from baza_wiedzy.modules.ingestion import chunks, language
from baza_wiedzy.modules.ingestion.extractors.base import (
    Collector,
    Context,
    LimitExceeded,
    Limits,
    PermanentError,
    find,
)
from baza_wiedzy.modules.ingestion.extractors.html_text import html_to_text
from baza_wiedzy.modules.ingestion.extractors.txt import decode
from fixtures.samples import (
    ENGLISH_TEXT,
    POLISH_TEXT,
    damaged_headers_eml,
    docx_bytes,
    eml_bytes,
    nested_eml,
    xxe_docx,
    zip_bomb_docx,
)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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


# --- TXT: encodings ------------------------------------------------------------------------

PANGRAM = "Zażółć gęślą jaźń. ŻÓŁTA ŹREBIĘ, Śląsk, Łódź – „cudzysłów”…"


@pytest.mark.parametrize(
    ("encoding", "label"),
    [
        ("utf-8", "utf-8"),
        ("cp1250", "windows-1250"),
        ("iso8859_2", "iso-8859-2"),
        ("utf-8-sig", "utf-8"),
        ("utf-16", "utf-16"),
    ],
)
def test_txt_encodings_keep_polish_letters(encoding: str, label: str) -> None:
    text = PANGRAM if encoding != "iso8859_2" else PANGRAM.replace("–", "-").replace("„", '"')
    text = text.replace("”", '"').replace("…", "...") if encoding == "iso8859_2" else text
    assert decode(text.encode(encoding)) == (text, label)


@pytest.mark.parametrize(
    ("word", "encoding", "label"),
    [
        ("źródło", "iso8859_2", "iso-8859-2"),
        ("źródło", "cp1250", "windows-1250"),
        ("ąś", "iso8859_2", "iso-8859-2"),
        ("ąś", "cp1250", "windows-1250"),
        ("Łódź", "iso8859_2", "iso-8859-2"),
    ],
)
def test_short_texts_choose_the_encoding_with_polish_letters(
    word: str, encoding: str, label: str
) -> None:
    assert decode(word.encode(encoding)) == (word, label)


@pytest.mark.parametrize(
    "data",
    [
        "Zażółć gęślą jaźń".encode("cp852"),  # DOS code page: not supported
        bytes(range(0xA0, 0x100)) * 4,  # no text at all
    ],
)
def test_unrecognised_encoding_fails_with_a_clear_message(data: bytes) -> None:
    with pytest.raises(PermanentError) as error:
        decode(data)
    assert error.value.code == "encoding"
    assert "UTF-8" in error.value.message


def test_txt_part_and_encoding_note(tmp_path: Path) -> None:
    result = run(tmp_path, "txt", "a.txt", PANGRAM.encode("cp1250"))
    assert [p.text for p in result.parts] == [PANGRAM]
    assert result.parts[0].part_type == "body" and result.parts[0].origin == []
    assert result.notes == [{"code": "encoding", "origin": [], "value": "windows-1250"}]


def test_text_limit(tmp_path: Path) -> None:
    with pytest.raises(LimitExceeded):
        run(tmp_path, "txt", "a.txt", b"a" * 2000, max_text_chars=1000)


# --- chunks and language --------------------------------------------------------------------


def test_long_text_is_split_into_chunks_under_the_limit() -> None:
    paragraph = ("Zdanie numer jeden w akapicie testowym. " * 50).strip()
    text = "\n\n".join(f"{n}. {paragraph}" for n in range(400))  # about 800,000 characters
    pieces = chunks.split(text)
    assert len(pieces) >= 8
    assert all(0 < len(piece) <= chunks.CHUNK_CHARS for piece in pieces)
    assert "".join(pieces).replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(
        " ", ""
    )


def test_text_without_spaces_is_cut_hard() -> None:
    pieces = chunks.split("x" * 250_001)
    assert [len(p) for p in pieces] == [100_000, 100_000, 50_001]


def test_short_text_is_one_chunk() -> None:
    assert chunks.split("krótki") == ["krótki"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (POLISH_TEXT, "pl"),
        (ENGLISH_TEXT, "en"),
        ("Umowa nr 17", "unknown"),
        ("1234 5678", "unknown"),
    ],
)
def test_language(text: str, expected: str) -> None:
    assert language.detect(text, min_letters=40) == expected


def test_language_threshold_is_configurable() -> None:
    assert language.detect("Zażółć gęślą jaźń", min_letters=10) == "pl"
    assert language.detect("Zażółć gęślą jaźń", min_letters=100) == "unknown"


# --- HTML -----------------------------------------------------------------------------------


def test_html_to_text_drops_active_content_and_keeps_text() -> None:
    html = (
        "<html><head><title>T</title><style>p{color:red}</style>"
        "<script>alert('x')</script></head><body>"
        "<p>Dzień&nbsp;dobry,<br>przesyłam <b>umowę</b> &amp; aneks.</p>"
        '<img src="https://tracker.example.com/pixel.gif" alt="">'
        '<a href="https://example.com/x">link</a>'
        "<table><tr><td>Pozycja</td><td>Kwota</td></tr></table>"
        "<ul><li>pierwszy</li><li>drugi</li></ul></body></html>"
    )
    text = html_to_text(html)
    assert "Dzień dobry,\nprzesyłam umowę & aneks." in text
    assert "Pozycja | Kwota" in text and "- pierwszy" in text and "link" in text
    for absent in ("alert", "color", "tracker", "https://", "<"):
        assert absent not in text


# --- EML ------------------------------------------------------------------------------------


def test_eml_body_headers_and_attachments(tmp_path: Path) -> None:
    data = eml_bytes(
        html="<p>wersja HTML</p>",
        attachments=[
            ("../../umowa‮txt.docx", docx_bytes([ENGLISH_TEXT]), DOCX),
            ("notatka.txt", "Notatka: źródło".encode("cp1250"), "text/plain"),
            ("logo.png", b"\x89PNG\r\n\x1a\n", "image/png"),
        ],
    )
    result = run(tmp_path, "eml", "mail.eml", data)
    body, docx_part, note_part = result.parts
    assert body.text == POLISH_TEXT and body.part_type == "body" and body.origin == []
    assert "wersja HTML" not in body.text  # plain text is preferred
    assert docx_part.text == ENGLISH_TEXT and docx_part.part_type == "attachment"
    assert docx_part.origin == [{"type": "attachment", "name": "umowatxt.docx"}]
    assert note_part.text == "Notatka: źródło"
    assert {"code": "skipped", "origin": [{"type": "attachment", "name": "logo.png"}],
            "reason": "unsupported"} in result.notes  # fmt: skip
    email = result.email
    assert email is not None
    assert email.subject == "Przegląd urządzeń – październik"
    assert email.from_addr == "Anna Przykładowa <anna@example.com>"
    assert email.to_addrs is not None and "jan@example.com" in email.to_addrs
    assert email.cc_addrs == "archiwum@example.com"
    assert email.message_id == "<synthetic-0001@example.com>"
    assert email.sent_at is not None and email.sent_at.year == 2026


def test_html_only_mail_is_converted_to_text(tmp_path: Path) -> None:
    data = eml_bytes(plain=None, html="<p>Treść <script>zly()</script>tylko w HTML</p>")
    result = run(tmp_path, "eml", "mail.eml", data)
    assert [p.text for p in result.parts] == ["Treść tylko w HTML"]


def test_damaged_headers_and_dates_are_tolerated(tmp_path: Path) -> None:
    result = run(tmp_path, "eml", "mail.eml", damaged_headers_eml())
    assert [p.text for p in result.parts] == [POLISH_TEXT]
    assert result.email is not None and result.email.sent_at is None


def test_nested_mails_stop_at_the_depth_limit(tmp_path: Path) -> None:
    result = run(tmp_path, "eml", "mail.eml", nested_eml(4), max_depth=3)
    assert [p.text for p in result.parts] == [
        "Treść poziomu 0.",
        "Treść poziomu 1.",
        "Treść poziomu 2.",
        "Treść poziomu 3.",
    ]
    assert [len(p.origin) for p in result.parts] == [0, 1, 2, 3]
    too_deep = [n for n in result.notes if n.get("reason") == "too_deep"]
    assert len(too_deep) == 1 and len(too_deep[0]["origin"]) == 4
    # Only the top-level message provides the metadata.
    assert result.email is not None and result.email.subject == "poziom 0"


def test_a_damaged_attachment_is_noted_not_fatal(tmp_path: Path) -> None:
    data = eml_bytes(attachments=[("zepsuty.docx", b"PK\x03\x04" + b"\0" * 100, DOCX)])
    result = run(tmp_path, "eml", "mail.eml", data)
    assert [p.text for p in result.parts] == [POLISH_TEXT]
    assert result.notes[-1]["code"] in ("skipped", "failed")


def test_attachment_temp_files_are_removed(tmp_path: Path) -> None:
    data = eml_bytes(attachments=[("a.txt", b"tekst", "text/plain")])
    run(tmp_path, "eml", "mail.eml", data)
    assert not any((tmp_path / "work").iterdir())


# --- DOCX -----------------------------------------------------------------------------------


def test_docx_paragraphs_table_and_header(tmp_path: Path) -> None:
    data = docx_bytes(
        ["Pierwszy akapit z polskimi znakami: ąęś.", "Drugi akapit."],
        table=[["Pozycja", "Kwota"], ["Przegląd", "1 200,00 zł"]],
        header="Nagłówek: Spółdzielnia",
    )
    text = run(tmp_path, "docx", "a.docx", data).parts[0].text
    assert text.startswith("Pierwszy akapit z polskimi znakami: ąęś.\nDrugi akapit.")
    assert "Pozycja | Kwota\nPrzegląd | 1 200,00 zł" in text
    assert "Nagłówek: Spółdzielnia" in text


def test_docx_zip_bomb_is_refused_before_unpacking(tmp_path: Path) -> None:
    with pytest.raises(PermanentError) as error:
        run(tmp_path, "docx", "a.docx", zip_bomb_docx())
    assert error.value.code == "zip_ratio"


def test_docx_external_entities_are_not_resolved(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TAJNE-DANE-SERWERA")
    result = run(tmp_path, "docx", "a.docx", xxe_docx(secret.as_uri()))
    assert "TAJNE" not in "".join(p.text for p in result.parts)


def test_damaged_docx_fails_cleanly(tmp_path: Path) -> None:
    with pytest.raises(PermanentError):
        run(tmp_path, "docx", "a.docx", b"PK\x03\x04" + b"\0" * 200)
