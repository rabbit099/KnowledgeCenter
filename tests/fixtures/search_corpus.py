"""Synthetic Polish-English corpus for search relevance (golden) tests and `make probki`.

One definition serves both: the golden tests (Phase 3, stage B) upload these files through the
real pipeline and check the queries below; `make probki` writes the same files and an
expected-results list for a manual test, so the two cannot drift apart. Every text is invented;
addresses use example.com (RFC 2606). Until the owner's real (non-confidential) documents and
queries arrive, this corpus is the relevance reference.

Covered: Polish inflection (umowa/umowy/umowie), diacritics and their absence (Łódź/lodz),
proper names, numbers and codes (contract and invoice numbers, a tax number, a server name),
quoted phrases, exclusion, bilingual queries, English plurals, words only in an e-mail
attachment or subject or sender, a partly scanned PDF, a word only in the file name, a typo in a
title, drafts and archived documents (visibility).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from fixtures.samples import docx_bytes, eml_bytes, text_pdf

NAMESPACE = uuid.UUID("6d1f7a52-3c1e-4b8e-9a51-7f3f0a2c9b10")
PDF_TYPE = "application/pdf"

# Who uploads (owner) in the golden tests; the manual test uses one Publisher account.
ANNA, BARTEK = "anna", "bartek"
PUBLISHED, DRAFT, ARCHIVED = "published", "draft", "archived"


@dataclass(frozen=True)
class CorpusDoc:
    key: str
    filename: str
    build: Callable[[], bytes]
    state: str = PUBLISHED
    owner: str = ANNA
    title: str = ""  # typed title; empty: file name (or e-mail subject)
    # Upload date in the golden tests (sorting by date, date filter); ISO date, UTC.
    date: str = "2026-10-01"

    @property
    def id(self) -> uuid.UUID:
        """Stable identifier of the corpus entry (not the database id)."""
        return uuid.uuid5(NAMESPACE, self.key)


@dataclass(frozen=True)
class GoldenQuery:
    query: str
    must: frozenset[str] = frozenset()  # keys that must be found
    must_not: frozenset[str] = frozenset()  # keys that must not be found
    top: str | None = None  # key expected as the first result
    as_user: str = "reader"  # reader (Czytelnik), anna (owner of the drafts), admin
    archived: bool = False  # the "show archived" filter
    note: str = ""  # what the case checks (Polish: shown in OCZEKIWANE-wyszukiwanie.txt)
    stage: str = "B"  # B: plain full-text search; trigram: needs title similarity (stage B)


def _txt(text: str) -> Callable[[], bytes]:
    return lambda: text.encode("utf-8")


def _docx(*paragraphs: str) -> Callable[[], bytes]:
    return lambda: docx_bytes(list(paragraphs))


def _pdf(*pages: str, scans: tuple[int, ...] = ()) -> Callable[[], bytes]:
    return lambda: text_pdf(list(pages), image_pages=scans)


def _mail(**kwargs: object) -> Callable[[], bytes]:
    return lambda: eml_bytes(**kwargs)


DOCS: tuple[CorpusDoc, ...] = (
    CorpusDoc(
        "umowa-serwisowa",
        "umowa-serwisowa-UM-2026-017.docx",
        _docx(
            "Umowa serwisowa nr UM/2026/017 zawarta w Łodzi pomiędzy Zakładem Usług "
            "Przykładowych a Spółdzielnią Mieszkaniową Zielony Zakątek.",
            "Wykonawca zobowiązuje się do comiesięcznego przeglądu urządzeń chłodniczych.",
        ),
        date="2026-03-02",
    ),
    CorpusDoc(
        "aneks",
        "aneks-2.txt",
        _txt(
            "Aneks nr 2 do umowy UM/2026/017. Strony zgodnie postanawiają, że w umowie "
            "zmienia się termin płatności z 14 na 30 dni."
        ),
        owner=BARTEK,
        title="Aneks nr 2",
        date="2026-06-15",
    ),
    CorpusDoc(
        "rejestr-umow",
        "rejestr.pdf",
        _pdf("Rejestr umów serwisowych prowadzi dział administracji. Ostatni wpis: lipiec."),
        title="Rejestr umów",
        date="2026-07-20",
    ),
    CorpusDoc(
        "lodz",
        "oddzial.txt",
        _txt("Oddział Łódź mieści się przy ulicy Piotrkowskiej 100. Godziny pracy 8-16."),
        title="Oddział Łódź",
    ),
    CorpusDoc(
        "faktura-0042",
        "FV-2026-11-0042.pdf",
        _pdf(
            "Faktura VAT nr FV/2026/11/0042 z dnia 3 listopada 2026. "
            "Sprzedawca: Zakład Usług Przykładowych, NIP 725-18-01-126."
        ),
        title="Faktura FV/2026/11/0042",
        date="2026-11-03",
    ),
    CorpusDoc(
        "faktura-0043",
        "FV-2026-11-0043.txt",
        _txt("Faktura VAT nr FV/2026/11/0043 z dnia 4 listopada 2026. Korekta ceny."),
        title="Faktura FV/2026/11/0043",
        date="2026-11-04",
    ),
    CorpusDoc(
        "procedura-bhp",
        "procedura-bhp.docx",
        _docx(
            "Obowiązują zasady bezpieczeństwa pracy przy urządzeniach elektrycznych.",
            "Przed rozpoczęciem pracy należy odłączyć zasilanie.",
        ),
        title="Procedura BHP",
    ),
    CorpusDoc(
        "magazyn",
        "magazyn.txt",
        _txt("Ogólne zasady wydawania sprzętu oraz instrukcja bezpieczeństwa magazynu."),
        title="Instrukcja magazynu",
    ),
    CorpusDoc(
        "backup-policy",
        "backup-policy.pdf",
        _pdf(
            "Backup policy. The server EX-PVE0 is backed up every night at 2:00. "
            "All backups are encrypted and kept for thirty days."
        ),
        title="Backup policy",
    ),
    CorpusDoc(
        "invoices-en",
        "invoice-approval.docx",
        _docx("All invoices must be approved by the finance department before payment."),
        title="Invoice approval",
    ),
    CorpusDoc(
        "contracts-en",
        "contract-review.txt",
        _txt("The legal team reviews every contract with a value above ten thousand euro."),
        title="Contract review",
    ),
    CorpusDoc(
        "dwujezyczny",
        "logowanie.txt",
        _txt(
            "Instrukcja logowania. Hasło musi mieć co najmniej 14 znaków.\n"
            "Login instructions. The password must have at least 14 characters."
        ),
        title="Logowanie / Login",
    ),
    CorpusDoc(
        "mail-klimatyzacja",
        "zlecenie.eml",
        _mail(
            subject="Przegląd klimatyzacji – zlecenie",
            plain="W załączniku protokół. Proszę o potwierdzenie terminu.",
            attachments=[
                (
                    "protokol.pdf",
                    text_pdf(["Protokół odbioru klimatyzatorów nr PR-88/2026."]),
                    PDF_TYPE,
                )
            ],
            sender="Serwis Chłodnictwa <serwis@example.com>",
        ),
        date="2026-09-10",
    ),
    CorpusDoc(
        "mail-ksiegowosc",
        "rozliczenie.eml",
        _mail(
            subject="Rozliczenie za październik",
            plain="Przesyłam zestawienie kosztów do akceptacji.",
            sender="Dział Księgowości <ksiegowosc@example.com>",
        ),
        date="2026-11-05",
    ),
    CorpusDoc(
        "szkic-regulamin",
        "regulamin.txt",
        _txt("Projekt regulaminu pracy zdalnej. Wersja robocza do konsultacji."),
        state=DRAFT,
        title="Regulamin pracy zdalnej (projekt)",
    ),
    CorpusDoc(
        "archiwalny-cennik",
        "cennik-2019.txt",
        _txt("Cennik usług serwisowych obowiązujący w 2019 roku."),
        state=ARCHIVED,
        title="Cennik 2019",
        date="2019-01-10",
    ),
    CorpusDoc(
        "inwentaryzacja",
        "inwentaryzacja.pdf",
        _pdf("Protokół z inwentaryzacji sprzętu w magazynie B.", "Skan podpisów", scans=(2,)),
        title="Inwentaryzacja",
    ),
    CorpusDoc(
        "nazwiska",
        "spotkanie.txt",
        _txt(
            "Notatka ze spotkania w sprawie dostaw. Uczestnicy: Grzegorz Brzęczyszczykiewicz, "
            "Anna Nowak."
        ),
        title="Notatka ze spotkania",
    ),
    CorpusDoc(
        "harmonogram",
        "harmonogram.txt",
        _txt("Przeglądy techniczne wind odbywają się co kwartał."),
        title="Harmonogram przeglądów technicznych",
    ),
    CorpusDoc(
        "plan-ewakuacji",
        "plan-ewakuacji-budynek-B.pdf",
        _pdf("Drogi wyjścia oznaczono zielonymi znakami. Punkt zbiórki: parking."),
        title="Drogi wyjścia – budynek B",  # the searched word is only in the file name
    ),
    # Identifiers (ADR-026): the same number written differently in documents and queries.
    CorpusDoc(
        "kontrahent",
        "kontrahent.txt",
        _txt("Karta kontrahenta: Hurtownia Przykładowa, NIP 7251801126, REGON 123456785."),
        title="Karta kontrahenta",
    ),
    CorpusDoc(
        "zlecenie-17",
        "zlecenie-kotlownia.txt",
        _txt("Zlecenie nr 17/2026 na przegląd kotłowni w budynku A."),
        title="Zlecenie przeglądu kotłowni",
    ),
    CorpusDoc(
        "umowa-um-17",
        "umowa-dostawa.txt",
        _txt("Umowa UM/17/2026 na dostawę materiałów biurowych."),
        title="Umowa na dostawę materiałów",
    ),
    CorpusDoc(
        "kontakt",
        "kontakt-awaryjny.txt",
        _txt("Kontakt w sprawie awarii: dyżurny technik, tel. 600 100 200 (całą dobę)."),
        title="Kontakt awaryjny",
    ),
    CorpusDoc(
        "recepcja",
        "recepcja.txt",
        _txt("Recepcja: +48 501 222 333, e-mail recepcja@biuro.example.com."),
        title="Recepcja",
    ),
    CorpusDoc(
        "zgloszenie",
        "zgloszenie-awarii.txt",
        _txt(
            "Zgłoszenie IT-002: serwer EX-PVE1 nie odpowiada pod adresem 192.168.10.25, "
            "strona intranet.example.com jest niedostępna."
        ),
        title="Zgłoszenie awarii",
    ),
)

KEYS = frozenset(doc.key for doc in DOCS)


def _q(
    query: str, must: tuple[str, ...] = (), must_not: tuple[str, ...] = (), **kw: object
) -> GoldenQuery:
    return GoldenQuery(query, frozenset(must), frozenset(must_not), **kw)  # type: ignore[arg-type]


QUERIES: tuple[GoldenQuery, ...] = (
    _q(
        "umowa",
        ("umowa-serwisowa", "aneks", "rejestr-umow"),
        note="odmiana: umowa, umowy, umowie, umów",
    ),
    _q("umowie", ("umowa-serwisowa", "aneks", "rejestr-umow"), note="zapytanie w innym przypadku"),
    _q("UM/2026/017", ("umowa-serwisowa", "aneks"), ("rejestr-umow",), note="numer umowy"),
    _q("Łódź", ("lodz", "umowa-serwisowa"), note="Łódź znajduje też „w Łodzi”"),
    _q(
        "lodz",
        ("lodz",),
        ("umowa-serwisowa",),
        note="bez polskich znaków: dokładnie to słowo; „w Łodzi” NIE jest znajdowane "
        "(udokumentowane ograniczenie, patrz uwagi)",
    ),
    _q("lodzi", ("umowa-serwisowa",), note="bez polskich znaków, forma z tekstu: znajduje"),
    _q("faktura -korekta", ("faktura-0042",), ("faktura-0043",), note="wykluczenie w dokumencie"),
    _q('"zasady bezpieczeństwa"', ("procedura-bhp",), ("magazyn",), note="fraza w cudzysłowie"),
    _q("bezpieczeństwo -magazyn", ("procedura-bhp",), ("magazyn",), note="wykluczenie minusem"),
    _q(
        "FV/2026/11/0042",
        ("faktura-0042",),
        ("faktura-0043",),
        top="faktura-0042",
        note="numer faktury",
    ),
    _q(
        "725-18-01-126", ("faktura-0042", "kontrahent"), note="NIP z myślnikami (tekst: oba zapisy)"
    ),
    _q("7251801126", ("faktura-0042", "kontrahent"), note="NIP bez myślników (tekst: oba zapisy)"),
    _q(
        "17/2026",
        ("zlecenie-17", "umowa-um-17"),
        ("umowa-serwisowa", "aneks"),
        note="numer umowy, także wewnątrz UM/17/2026; nie UM/2026/017",
    ),
    _q("UM/17/2026", ("umowa-um-17",), ("zlecenie-17", "umowa-serwisowa"), note="pełny numer"),
    _q("UM-17-2026", ("umowa-um-17",), note="numer z innymi separatorami"),
    _q("umowie 17/2026", ("umowa-um-17",), note="słowo w innej formie i numer"),
    _q("600 100 200", ("kontakt",), note="telefon ze spacjami"),
    _q("600100200", ("kontakt",), note="telefon bez spacji"),
    _q("+48 600 100 200", ("kontakt",), note="telefon z kierunkowym (tekst bez)"),
    _q("501222333", ("recepcja",), note="telefon bez kierunkowego (tekst z +48)"),
    _q("+48501222333", ("recepcja",), note="telefon z kierunkowym, bez spacji"),
    _q("IT-002", ("zgloszenie",), note="kod zgłoszenia"),
    _q("IT002", ("zgloszenie",), note="kod bez myślnika"),
    _q("192.168.10.25", ("zgloszenie",), note="adres IP"),
    _q("192.168.10", ("zgloszenie",), note="początek adresu IP (trzy oktety)"),
    _q("intranet.example.com", ("zgloszenie",), note="nazwa hosta"),
    _q(
        "example.com",
        ("zgloszenie", "mail-ksiegowosc", "recepcja"),
        note="domena: host, nadawca maila, adres w tekście",
    ),
    _q("recepcja@biuro.example.com", ("recepcja",), note="adres e-mail w tekście"),
    _q("ksiegowosc", ("mail-ksiegowosc",), note="część adresu przed @ (nadawca)"),
    _q("EX-PVE0", ("backup-policy",), ("zgloszenie",), note="nazwa serwera (nie EX-PVE1)"),
    _q("backups", ("backup-policy",), note="angielska liczba mnoga"),
    _q("invoice", ("invoices-en",), note="angielska liczba pojedyncza znajduje mnogą"),
    _q("contracts", ("contracts-en",), note="angielska liczba mnoga znajduje pojedynczą"),
    _q("hasło password", ("dwujezyczny",), note="zapytanie dwujęzyczne (oba słowa)"),
    _q("PR-88/2026", ("mail-klimatyzacja",), note="numer tylko w załączniku PDF maila"),
    _q("klimatyzatory", ("mail-klimatyzacja",), note="słowo tylko w załączniku, inna forma"),
    _q("październik", ("mail-ksiegowosc",), note="temat maila"),
    _q("ksiegowosc@example.com", ("mail-ksiegowosc",), note="adres nadawcy"),
    _q("regulamin", (), ("szkic-regulamin",), note="szkic niewidoczny dla czytelnika"),
    _q(
        "regulamin",
        ("szkic-regulamin",),
        as_user=ANNA,
        note="autor widzi swój szkic (oznaczony „szkic”)",
    ),
    _q(
        "cennik",
        (),
        ("archiwalny-cennik",),
        as_user="admin",
        note="zarchiwizowane domyślnie ukryte",
    ),
    _q(
        "cennik",
        ("archiwalny-cennik",),
        as_user="admin",
        archived=True,
        note="filtr „pokaż zarchiwizowane”",
    ),
    _q(
        "cennik",
        (),
        ("archiwalny-cennik",),
        archived=True,
        note="czytelnik nie widzi zarchiwizowanych",
    ),
    _q("inwentaryzacja", ("inwentaryzacja",), note="PDF częściowo skanowany: strona z tekstem"),
    _q("Brzęczyszczykiewicz", ("nazwiska",), note="nazwisko spoza słownika"),
    _q("brzeczyszczykiewicz", ("nazwiska",), note="nazwisko bez polskich znaków"),
    _q("ewakuacji", ("plan-ewakuacji",), note="słowo tylko w nazwie pliku"),
    _q("harmonogran", ("harmonogram",), stage="trigram", note="literówka w tytule"),
)


def expected_text(directory: str = "wyszukiwanie") -> str:
    """OCZEKIWANE-wyszukiwanie.txt for `make probki` (Polish: for the manual test)."""
    by_key = {doc.key: doc for doc in DOCS}
    state = {
        PUBLISHED: "opublikuj",
        DRAFT: "zostaw jako szkic",
        ARCHIVED: "opublikuj, potem zarchiwizuj",
    }
    lines = [
        "Próbki do testu wyszukiwania (syntetyczne, Faza 3).",
        "",
        f"1. Wgraj wszystkie pliki z katalogu {directory}/ jako konto z rolą Publikujący",
        "   (np. bw-test), wpisując podany tytuł (bez tytułu: nazwa pliku albo temat maila),",
        "   i ustaw stan przyciskami na stronie dokumentu zgodnie z listą poniżej.",
        "2. Poczekaj, aż wszystkie mają stan przetwarzania „gotowe” (inwentaryzacja.pdf:",
        "   „wymaga OCR”), i wyszukuj jako konto z rolą Czytelnik (bw-test2), chyba że",
        "   podano inaczej (autor = konto, które wgrało pliki; Admin = administrator).",
        "3. „Znajduje” = te pliki są na liście wyników (mogą być też inne dokumenty z bazy);",
        "   „nie znajduje” = tego pliku nie ma na liście.",
        "",
        "Pliki i stan po wgraniu:",
    ]
    for doc in DOCS:
        title = f"; przy wgraniu wpisz tytuł: {doc.title}" if doc.title else ""
        lines.append(f"  {doc.filename:<36} {state[doc.state]}{title}")
    lines += ["", "Zapytania (wpisz dokładnie tekst w nawiasach kątowych):"]
    who = {"reader": "Czytelnik", ANNA: "autor (Publikujący)", "admin": "Admin"}
    for case in QUERIES:
        found = ", ".join(by_key[k].filename for k in sorted(case.must)) or "nic z tych plików"
        missing = ", ".join(by_key[k].filename for k in sorted(case.must_not))
        extra = " + filtr „pokaż zarchiwizowane”" if case.archived else ""
        lines.append(f"  <{case.query}>  jako {who[case.as_user]}{extra}")
        lines.append(f"      znajduje: {found}")
        if missing:
            lines.append(f"      nie znajduje: {missing}")
        lines.append(f"      ({case.note})")
    lines += [
        "",
        "Uwagi:",
        "  - Zapytanie bez polskich znaków znajduje dokładnie to słowo („lodz” → „Łódź”),",
        "    ale nie jego odmiany („Łodzi”): odmianę rozpoznaje tylko zapytanie z polskimi",
        "    znakami. To ograniczenie słownika (ADR-003, ADR-016).",
        "    Zapytanie „lodzi” znajduje „w Łodzi”, bo to ta sama forma bez znaków.",
        "  - Nazwiska spoza słownika są znajdowane w formie z tekstu; „Kowalski” nie",
        "    znajdzie „Kowalskim”.",
        "  - Numery i kody: znajdowane z separatorami i bez („725-18-01-126” = „7251801126”,",
        "    „600 100 200” = „600100200”), także fragment między separatorami („17/2026”",
        "    w „UM/17/2026”). Nie działa: część jednego członu („0042” w „00042”), początek",
        "    numeru („7251*”), fragment niekończący się na separatorze („1002”).",
        "  - Wyniki zależą od wszystkich dokumentów w bazie; testuj na czystej instalacji.",
        "",
    ]
    return "\n".join(lines)
