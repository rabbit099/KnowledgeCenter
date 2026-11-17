# Przetwarzanie dokumentów i OCR

> Dotyczy: v0.2.1 · Ostatnia aktualizacja: 2026-11-17

Jak wgrany plik staje się przeszukiwalnym tekstem. Walidacja uploadu i zapis plików należą do modułu `documents`; opisane tu przetwarzanie należy do modułu `ingestion` (zobacz [modules/ingestion.md](modules/ingestion.md)).

## Obsługiwane formaty

| Format | Ekstraktor | Stan |
|---|---|---|
| TXT | własna reguła kodowania: UTF-8, Windows-1250, ISO-8859-2 ([Kodowanie tekstu](#kodowanie-tekstu), ADR-022) | Faza 2, etap B |
| EML | `email` ze standardowej biblioteki (policy=default): nagłówki, treść tekstowa (treść HTML zamieniana na tekst), załączniki | Faza 2, etap B |
| DOCX | python-docx (akapity i tabele w kolejności, nagłówki, stopki) + zapasowo utwardzony odczyt surowego XML | Faza 2, etap B |
| PDF z tekstem | pypdfium2, jedna część na stronę | Faza 2, etap C |
| DOC | konwersja LibreOffice headless do tekstu (izolowany profil, limit czasu) | Faza 2, etap C |
| MSG | `extract-msg` | Faza 2, etap C |
| PDF skan | OCRmyPDF + Tesseract (`pol+eng`); do tego czasu strony bez tekstu są tylko oznaczane „wymaga OCR” | Faza 4 |

Załączniki EML/MSG, które same są obsługiwanymi plikami, są wyciągane rekurencyjnie (głębokość ≤ `INGEST_MAX_DEPTH`, domyślnie 3) i zapisywane jako części typu `attachment` w `ing_parts`. Pozostałe załączniki (obrazy, archiwa) są pomijane z adnotacją na stronie dokumentu. OCR samodzielnych obrazów jest poza zakresem MVP.

## Walidacja uploadu (w `app`)

Strona „Moje dokumenty” → „Wgraj dokument” (`/documents/upload`, uprawnienie `documents.upload`). Wgrywanie wymaga JavaScriptu: HTMX wysyła token CSRF w nagłówku `X-CSRF-Token` (middleware CSRF nie czyta ciał multipart); bez JavaScriptu strona pokazuje komunikat w `<noscript>`.

1. Rozmiar: żądanie jest odrzucane wcześnie, gdy `Content-Length` przekracza `MAX_UPLOAD_MB` (domyślnie 50), a kopiowanie przerywa się, gdy tylko faktyczny rozmiar go przekroczy (HTTP 413, „Plik jest większy niż dozwolony rozmiar.”). Proxy odrzuca ciała większe niż `MAX_REQUEST_MB` (domyślnie 51: plik plus narzut multipart); trzymaj go o jeden MB powyżej `MAX_UPLOAD_MB`.
2. Ciało parsuje Starlette (najwyżej 1 MB w pamięci, reszta w tymczasowym pliku buforowym, nigdy cały plik w pamięci); plik jest kopiowany porcjami po 1 MB do `/data/storage/.incoming/`, a w trakcie liczone są SHA-256 i rozmiar.
3. Typ **po zawartości**, nigdy po rozszerzeniu ani `Content-Type` (ADR-021): rozszerzenie musi być na liście dozwolonych (pdf, docx, doc, txt, eml, msg) **i** zgadzać się z wykrytym typem:

   | Typ | Sprawdzenie |
   |---|---|
   | PDF | `%PDF-` w pierwszych 1024 bajtach |
   | DOCX | ZIP, którego katalog centralny zawiera `[Content_Types].xml` i `word/document.xml` (`.xlsx` ze zmienioną nazwą jest odrzucany) |
   | DOC | kontener OLE2 ze strumieniem `WordDocument` |
   | MSG | kontener OLE2 ze strumieniem `__properties_version1.0` |
   | EML | blok nagłówków RFC 5322 z `From` i co najmniej jednym innym nagłówkiem poczty, bez bajtów NUL |
   | TXT | brak bajtów NUL w pierwszych 64 KB (UTF-16 z BOM dozwolony) |

   Każdy inny plik OLE2 (np. stary `.xls` ze zmienioną nazwą na `.doc`) i każda niezgodność są odrzucane (HTTP 422, „Zawartość pliku nie pasuje do jego rozszerzenia albo plik jest uszkodzony.”); plik przychodzący jest usuwany.
4. Plik jest przenoszony atomową zmianą nazwy do `originals/<aa>/<bb>/<klucz>` (klucz = losowy UUID) i ustawiany tylko do odczytu. Nazwa pliku od użytkownika jest oczyszczana (części katalogów, znaki sterujące i dwukierunkowe usunięte, NFC, najwyżej 255 znaków) i zostaje tylko metadaną.
5. Powstaje dokument-szkic z wersją 1 (`doc_documents`, `doc_versions`, `processing_status = pending`); tytuł:
   - pole „Tytuł”, gdy jest wypełnione: dokładnie tak, jak wpisano, tylko z ujednoliconymi spacjami i najwyżej 300 znaków;
   - w przeciwnym razie nazwa pliku bez rozszerzenia (`Umowa_SERWIS-2026.pdf` → `Umowa_SERWIS-2026`);
   - dla EML i MSG bez wpisanego tytułu przetwarzanie zastępuje go tematem wiadomości (z tym samym czyszczeniem); do tego czasu, albo gdy temat jest pusty, zostaje nazwa pliku.

   Wielkość liter nigdy nie jest zmieniana: tytuł ma wielkość liter tego, co wpisano, nazwy pliku albo tematu. Pochodzenie tytułu jest zapisane w `doc_documents.title_source` (`user`, `filename`, `subject`), więc wpisany tytuł nigdy nie zostaje zastąpiony.
6. Duplikaty: strona dokumentu pokazuje „Możliwy duplikat” z odnośnikami, gdy inny dokument **widoczny dla wgrywającego** ma wersję o tym samym SHA-256. Cudze szkice nigdy nie są porównywane, więc ostrzeżenie niczego nie zdradza. Wgranie nie jest blokowane; usuwanie duplikatu dojdzie z cyklem życia (Faza 5).
7. Audyt `document.upload` z identyfikatorem dokumentu, `version_id`, `sha256`, `size_bytes` i `kind` (bez nazwy pliku i treści). Po zatwierdzeniu publikowane jest zdarzenie `document.version.uploaded`, a moduł `ingestion` kolejkuje przetwarzanie.
8. Skanowanie antywirusowe: krok pipeline przetwarzania, domyślnie wyłączony ([Hook skanowania antywirusowego](#hook-skanowania-antywirusowego)); ClamAV nie jest podłączany w Fazie 2.

Szkice widzi tylko wgrywający i posiadacze `documents.read_drafts` (Admin); każdy inny dostaje **404** na stronie dokumentu, więc jego istnienie nie jest ujawniane.

## Przetwarzanie (w `worker`)

Wgranie publikuje `document.version.uploaded`; moduł `ingestion` kolejkuje zadanie Dramatiq `ingestion.process_version` (kolejka `ingestion`). Jeśli Redis jest w tym momencie niedostępny, wersja po prostu zostaje w stanie „oczekuje”, a najbliższy start workera ją zakolejkuje.

Status wersji (`doc_versions.processing_status`, należy do `documents`, zmieniany wyłącznie przez `documents.services.api`; w nawiasach etykiety w interfejsie):

```
pending (oczekuje) --claim--> processing (przetwarzanie) --> done (gotowe) | needs_ocr (wymaga OCR)
                                         |--> failed (błąd)                 błąd trwały, bez ponawiania
pending <---- ponowienie z opóźnieniem --|                                  błąd przejściowy
processing dłużej niż INGEST_STALE_MINUTES --> pending albo failed po ostatniej próbie
done | failed | needs_ocr --„Przetwórz ponownie”--> pending (licznik prób od zera)
```

Jedna próba (`ingestion/pipeline.py`):
1. **Przejęcie** wersji pod blokadą wiersza: tylko `pending` (albo przeterminowane `processing`) przechodzi w `processing`, licznik prób rośnie o jeden. Zdublowane zadanie albo wersja już przetworzona nic nie robią.
2. **Hook skanowania antywirusowego** (`SCAN_HOOK`, domyślnie `none`, niżej).
3. **Ekstrakcja** oryginału (tylko do odczytu) w prywatnym katalogu tymczasowym `/data/storage/.work/<id>-…` na wolumenie storage; katalog jest potem usuwany.
4. **Podział**: teksty dłuższe niż 100 000 znaków są dzielone na granicy akapitu, w razie braku linii, w razie braku spacji (`chunk_no` 1, 2, …), więc każdy tsvector zostaje daleko poniżej limitu 1 MB.
5. **Język** każdego kawałka.
6. **Zapis w jednej transakcji**: usunięcie starych części wersji, zapis nowych części (`ing_parts`) i adnotacji (`ing_results`), nagłówków maila (`doc_email_metadata`, przez `documents.services.api`), statusu końcowego i wpisu outbox. Dwukrotne przetworzenie wersji nigdy więc nie dubluje części. Jeśli w trakcie tej próby wersję przetworzono ponownie albo odzyskano, jej wynik jest odrzucany.
7. **Dostarczenie** wpisu outbox: na magistrali zdarzeń publikowane jest `document.version.extracted` (`document_id`, `version_id`, nic więcej).

Strona dokumentu odpytuje status co 3 s, dopóki jest „oczekuje” albo „przetwarzanie”, a potem pokazuje nagłówki maila, adnotacje i wyodrębnione części (pochodzenie, język, rozmiar, podgląd pierwszych 5000 znaków).

### Błędy i ponawianie

| Rodzaj | Przykłady | Wynik |
|---|---|---|
| Trwały | uszkodzony plik, nierozpoznane kodowanie tekstu, bomba dekompresyjna, więcej tekstu lub części niż limit, typ jeszcze nieprzetwarzany | od razu `failed` z czytelnym komunikatem, bez ponawiania |
| Przejściowy | baza lub Redis niedostępne, przekroczony limit czasu `INGEST_TIME_LIMIT_SECONDS` (900 s), zabity proces zewnętrzny | powrót do `pending` („Chwilowy problem; przetwarzanie zostanie automatycznie ponowione.”) i ponowne zakolejkowanie po `INGEST_RETRY_MIN_SECONDS × 2^(próba−1)`, najwyżej `INGEST_RETRY_MAX_SECONDS` (30 s, 60 s, 120 s, … 600 s); po `INGEST_MAX_ATTEMPTS` (3) próbach `failed` |
| Nieoczekiwany (błąd w kodzie) | awaria parsera | `failed` z komunikatem o błędzie wewnętrznym; w logu klasa wyjątku i ramki stosu |

Uszkodzony albo nieobsługiwany załącznik nie powoduje błędu całego maila: jest pomijany z adnotacją. Przekroczenie limitów (tekst, części) zawsze kończy błędem całej wersji.

Komunikaty dla użytkownika mówią tylko o rodzaju problemu. Logi zawierają identyfikatory, typy plików, klasy wyjątków, liczby i ramki stosu (plik, wiersz, funkcja) — nigdy tekstu dokumentów, nazw plików, nagłówków maili ani treści wyjątków, które mogłyby cytować zawartość. Audyt zapisuje `document.reprocess` z identyfikatorem dokumentu, `version_id` i `sha256`.

### Kodowanie tekstu

Pliki TXT i tekstowe załączniki są dekodowane według stałej reguły (ADR-022):
1. Znacznik kolejności bajtów (BOM) rozstrzyga (UTF-8, UTF-16).
2. Poprawne UTF-8 (także czyste ASCII) to UTF-8.
3. W przeciwnym razie próbowane są Windows-1250 i ISO-8859-2. Bajty 0x80–0x9F wykluczają ISO-8859-2 (są tam znakami sterującymi). Wygrywa kandydat z większą liczbą polskich liter; przy remisie Windows-1250.
4. Sprawdzenie wiarygodności: co najmniej 80% znaków spoza ASCII w wyniku musi być polskimi literami albo typowymi znakami typograficznymi (cudzysłowy, myślniki, `…`, `€`, `§`, twarda spacja, …).

Jeśli sprawdzenie nie przejdzie (na przykład strona kodowa DOS CP852 albo dane binarne), wersja kończy się błędem „Nie rozpoznano kodowania tekstu (obsługiwane: UTF-8, Windows-1250, ISO-8859-2). Zapisz plik w UTF-8 i wgraj go ponownie.” Świadomie nie ma zgadywania: błędny wybór zaindeksowałby śmieci. Wykryte kodowanie widać w adnotacji na stronie dokumentu.

### E-mail

- **Treść**: preferowany jest `text/plain`; treść wyłącznie w HTML jest zamieniana na tekst parserem ze standardowej biblioteki — nic nie jest uruchamiane ani pobierane, skrypty i style są usuwane, z odnośników zostaje tylko widoczny tekst.
- **Nagłówki** wiadomości głównej (temat, nadawca, odbiorcy, DW, data, Message-ID) trafiają do `doc_email_metadata` przez `documents.services.api` i są widoczne na stronie dokumentu; nigdy nie trafiają do logów. Uszkodzony nagłówek albo data dają pustą wartość; przetwarzanie trwa dalej.
- **Załączniki**: każda pozostała część oraz załączone wiadomości (`message/rfc822`) jako zagnieżdżone pliki `.eml`. Nazwy są oczyszczane jak nazwy wgrywanych plików; typ jest sprawdzany po zawartości jak przy wgrywaniu (rozszerzenie musi pasować). Każdy jest wyciągany z pochodzeniem „załącznik: nazwa › …”. Zagnieżdżenie głębsze niż `INGEST_MAX_DEPTH` (3), nieobsługiwane typy (obrazy, archiwa) i typy jeszcze nieprzetwarzane są pomijane z adnotacją. Części tekstowe bez nazwy pliku to alternatywne wersje treści, nie załączniki.

### Język

Każdy kawałek dostaje `pl`, `en` albo `unknown` z lingua, ograniczonej do polskiego i angielskiego i oceniającej pierwsze 20 000 znaków. Kawałek z mniej niż `LANGUAGE_MIN_LETTERS` (domyślnie 40) literami dostaje `unknown` zamiast zgadywania. Wynik jest informacyjny i służy jako filtr; nigdy nie wybiera indeksu wyszukiwania.

### Outbox i odzyskiwanie

Zdarzenie `document.version.extracted` jest zapisywane w `ing_outbox` w tej samej transakcji co części i dostarczane zaraz po zatwierdzeniu (co najmniej raz; handlery są idempotentne). Niedostarczone wpisy są dostarczane ponownie przez `SELECT … FOR UPDATE SKIP LOCKED`, więc dwa procesy nigdy nie wezmą tego samego wpisu.

Przy każdym starcie workera (kroki 2–4 wykonuje jeden proces na start, chroniony blokadą w Redis na 60 s):
1. Katalogi tymczasowe w `/data/storage/.work` starsze niż dwukrotny limit czasu (co najmniej godzina) są usuwane.
2. Wersje w `processing` dłużej niż `INGEST_STALE_MINUTES` (30) wracają do kolejki albo do `failed` („Przetwarzanie zostało przerwane zbyt wiele razy…”), gdy wykorzystały wszystkie próby.
3. Wersje w `pending` są kolejkowane ponownie (wersja już zakolejkowana jest przez drugie zadanie pomijana).
4. Niedostarczone zdarzenia są dostarczane; dostarczone starsze niż `OUTBOX_RETENTION_DAYS` (7) są usuwane.

To samo na żądanie: `baza-wiedzy reprocess --stuck` (kroki 2–3) i `baza-wiedzy events redeliver` (krok 4).

### Hook skanowania antywirusowego

Przed ekstrakcją pipeline wywołuje `ingestion/scanning.py` ze ścieżką oryginału. Przy `SCAN_HOOK=none` (domyślnie i na razie jedyna wartość) nic nie robi. W tym miejscu doszedłby skaner ClamAV (usługa `clamav` z profilu Compose `antivirus`, `INSTREAM`): zainfekowany plik zgłasza błąd trwały (wersja kończy się błędem, nic nie jest wyciągane), niedostępny skaner błąd przejściowy (ponawiany). To, czy wymaga tego polityka firmy, jest otwartym pytaniem 6 w [decisions.md](decisions.md).

## OCR

Decyzja dla każdego PDF:
- Wyciągnij tekst strona po stronie przez pypdfium2.
- Strona „wymaga OCR”, gdy ma mniej niż `OCR_MIN_CHARS` znaków niebiałych (domyślnie 25) i zawiera obraz.
- Jeśli jakakolwiek strona wymaga OCR, uruchom OCRmyPDF z `--skip-text` (strony, które mają tekst, zostają nietknięte), językami `pol+eng`, `--rotate-pages`, `--deskew`, limitem zadań równoległych i timeoutem (`OCR_TIMEOUT_SECONDS`, domyślnie 1800).
- Wyciągnij tekst ponownie ze stron po OCR, ustaw `ocr_applied = true` na tych częściach i na wersji.
- Zapisz powstały PDF jako plik pochodny (kopia z warstwą tekstu). Oryginał zostaje nietknięty i to jego zwraca „pobierz”.

Znane ograniczenia, o których warto powiedzieć użytkownikom: jakość OCR zależy od jakości skanu; pismo odręczne nie jest obsługiwane; bardzo duże skany mogą trwać minuty; PDF-y zabezpieczone hasłem kończą się komunikatem „PDF jest zabezpieczony hasłem”.

## Limity bezpieczeństwa

| Limit | Domyślnie | Ustawienie |
|---|---|---|
| Maksymalny rozmiar uploadu | 50 MB (proxy: `MAX_REQUEST_MB` 51) | `MAX_UPLOAD_MB` |
| Maksymalna głębokość załączników maila | 3 | `INGEST_MAX_DEPTH` |
| Maksymalna liczba części (kawałków) na wersję | 2000 | `INGEST_MAX_PARTS` |
| Maksymalna ilość wyciągniętego tekstu na wersję | 50 MB (znaków) | `INGEST_MAX_TEXT_MB` |
| ZIP (DOCX): liczba wpisów, łączny rozmiar po rozpakowaniu, współczynnik wpisów powyżej 1 MB | 5000, 200 MB, 100× (sprawdzane przed rozpakowaniem) | stałe w `extractors/docx.py` |
| XML (DOCX) | bez DTD, bez encji, bez sieci | stałe |
| Limit czasu jednej próby | 900 s | `INGEST_TIME_LIMIT_SECONDS` |
| Próby (tylko błędy przejściowe) | 3, opóźnienie od 30 s, podwajane do 600 s | `INGEST_MAX_ATTEMPTS`, `INGEST_RETRY_MIN_SECONDS`, `INGEST_RETRY_MAX_SECONDS` |
| Limit czasu LibreOffice | 120 s | Faza 2, etap C |
| Limit czasu OCR | 1800 s | Faza 4 |
| Procesy workera (po jednym zadaniu) | 2 | `WORKER_PROCESSES` |

Każda próba działa we własnym katalogu tymczasowym w `/data/storage/.work`, usuwanym po zakończeniu. Kontener workera ma limit pamięci (`WORKER_MEMORY_LIMIT`).

## Utrzymanie

- **Przetwórz ponownie**: przycisk „Przetwórz ponownie” pod wyodrębnionym tekstem (właściciel z `documents.upload` dla własnych dokumentów, `documents.edit_any` dla dowolnych) albo `baza-wiedzy reprocess <ID dokumentu lub wersji> …` / `baza-wiedzy reprocess --failed` w kontenerze `app`. Oba zerują licznik prób i trafiają do audytu (`document.reprocess`). W trakcie trwającej próby wersji nie da się przetworzyć ponownie („Dokument jest właśnie przetwarzany…”).
- **Zawieszone w „przetwarzanie”** (zabity worker): `baza-wiedzy reprocess --stuck` albo restart workera. Oczekiwany wynik: `Stuck versions: 1 queued again, 0 failed (no attempts left); waiting versions queued again: 0.`
- **Outbox zdarzeń**: `baza-wiedzy events redeliver` → `Events delivered: N, failed: 0, still undelivered: 0; old delivered entries removed: M.` Kod wyjścia 1, gdy handler zgłosił błąd (szczegóły w logu workera lub CLI: zdarzenie i klasa wyjątku).
- Po zmianie ekstraktorów lub konfiguracji wyszukiwania może być potrzebne `baza-wiedzy reindex-search` (zobacz [search.md](search.md), Faza 3) albo ponowne przetworzenie.
- Powód błędu widzi każdy, kto widzi dokument; szczegóły są w logu workera (`make logs`, szukaj identyfikatora wersji).

## Testy

Wszystkie pliki testowe są syntetyczne i generowane w testach (`tests/fixtures/samples.py`, `tests/fixtures/ole2.py`); w repozytorium nie ma żadnych binariów.

- Jednostkowe (`tests/unit/test_ingestion_extractors.py`): TXT w UTF-8, UTF-8 z BOM, UTF-16, Windows-1250 i ISO-8859-2 (także dwuliterowe teksty), odrzucone CP852 i dane binarne; limit tekstu; podział 800 000 znaków i tekstu bez spacji; język (pl, en, krótki → unknown, konfigurowalny próg); HTML na tekst (skrypty, style, obrazek śledzący, odnośniki); EML z alternatywą HTML, załącznikami DOCX/TXT/PNG, oczyszczonymi nazwami załączników, uszkodzonymi nagłówkami i datami, wiadomościami zagnieżdżonymi głębiej niż 3, uszkodzonym załącznikiem; DOCX z tabelą i nagłówkiem, bomba ZIP, encja zewnętrzna (XXE), uszkodzony plik.
- Integracyjne (`tests/integration/test_ingestion.py`, `make test-db`, prawdziwy worker na Redis): TXT i EML z załącznikiem DOCX od wgrania do „gotowe” z częściami, językiem, nagłówkami maila, adnotacjami i dostarczonym zdarzeniem; długi TXT w trzech kawałkach; dwukrotne przetworzenie bez duplikatów; błąd trwały bez ponawiania; błędy przejściowe z narastającym opóźnieniem i rezygnacją; błędy nieoczekiwane bez treści wyjątku w logu; logi bez tekstu, nazw i nagłówków; odzyskiwanie zawieszonych; odrzucanie wyniku zastąpionej próby; outbox ze `SKIP LOCKED`, nieudanymi handlerami i czyszczeniem; start workera (pliki tymczasowe, oczekujące wersje, jedno odzyskiwanie na start); uprawnienia „Przetwórz ponownie” (404/403/dozwolone, w trakcie) i CLI.
- `make smoke` wgrywa przez proxy mały plik TXT i czeka na „gotowe”.
- Jeszcze do zrobienia (etap C): PDF z tekstem, PDF bez tekstu, PDF zaszyfrowany, DOC, MSG z załącznikiem.
