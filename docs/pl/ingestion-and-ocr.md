# Przetwarzanie dokumentów i OCR

> Dotyczy: v0.4.1 · Ostatnia aktualizacja: 2026-11-20

Jak wgrany plik staje się przeszukiwalnym tekstem. Walidacja uploadu i zapis plików należą do modułu `documents`; opisane tu przetwarzanie należy do modułu `ingestion` (zobacz [modules/ingestion.md](modules/ingestion.md)).

## Obsługiwane formaty

| Format | Ekstraktor | Stan |
|---|---|---|
| TXT | własna reguła kodowania: UTF-8, Windows-1250, ISO-8859-2 ([Kodowanie tekstu](#kodowanie-tekstu), ADR-022) | Faza 2 |
| EML | `email` ze standardowej biblioteki (policy=default): nagłówki, treść tekstowa (treść HTML zamieniana na tekst), załączniki | Faza 2 |
| DOCX | python-docx (akapity i tabele w kolejności, nagłówki, stopki) + zapasowo utwardzony odczyt surowego XML | Faza 2 |
| PDF z tekstem | pypdfium2 w procesie potomnym, jedna część na stronę | Faza 2 |
| DOC | LibreOffice headless konwertuje do DOCX w procesie potomnym, potem ekstraktor DOCX | Faza 2 |
| MSG | `extract-msg`: nagłówki, treść tekstowa / HTML / RTF, załączniki, załączone wiadomości | Faza 2 |
| PDF skan | strony bez warstwy tekstu są rozpoznawane przez OCR (OCRmyPDF + Tesseract, `pol+eng`), także w załącznikach maili; tylko tekst | Faza 4, etap A |

Załączniki EML/MSG, które same są obsługiwanymi plikami, są wyciągane rekurencyjnie (głębokość ≤ `INGEST_MAX_DEPTH`, domyślnie 3) i zapisywane jako części typu `attachment` (albo `page` dla stron PDF) w `ing_parts`; każda załączona wiadomość Outlooka to jeden poziom. Pozostałe załączniki (obrazy, archiwa) oraz zabezpieczone hasłem są pomijane z adnotacją na stronie dokumentu. OCR samodzielnych obrazów jest poza zakresem MVP.

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

Strona dokumentu odpytuje status co 3 s, dopóki jest „oczekuje” albo „przetwarzanie” (i aktualizuje tytuł, który przetwarzanie może zmienić), a potem pokazuje nagłówki maila, adnotacje i wyodrębnione części (pochodzenie, język, rozmiar, podgląd pierwszych 5000 znaków; zeskanowana strona bez tekstu jest pokazywana jako „brak warstwy tekstowej, wymaga OCR”).

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
- **Nagłówki** wiadomości głównej (temat, nadawca, odbiorcy, DW, data, Message-ID) trafiają do `doc_email_metadata`. Adresy są zapisywane w jednym formacie dla EML i MSG: `Nazwa <adres>` (sam adres, gdy wiadomość nie podaje nazwy), kilka oddzielonych „, ”; UDW nie jest zapisywane. Dokumenty przetworzone przed 2026-11-17 mają stary format do czasu „Przetwórz ponownie”. Nagłówki są zapisywane przez `documents.services.api` i widoczne na stronie dokumentu; nigdy nie trafiają do logów. Uszkodzony nagłówek albo data dają pustą wartość; przetwarzanie trwa dalej.
- **Załączniki**: każda pozostała część oraz załączone wiadomości (`message/rfc822`) jako zagnieżdżone pliki `.eml`. Nazwy są oczyszczane jak nazwy wgrywanych plików; typ jest sprawdzany po zawartości jak przy wgrywaniu (rozszerzenie musi pasować). Każdy jest wyciągany z pochodzeniem „załącznik: nazwa”; zagnieżdżone są pokazywane jako „załącznik: a.msg › b.docx › strona 2” (słowo raz, przed pierwszym załącznikiem). Zagnieżdżenie głębsze niż `INGEST_MAX_DEPTH` (3), nieobsługiwane typy (obrazy, archiwa) i typy jeszcze nieprzetwarzane są pomijane z adnotacją. Części tekstowe bez nazwy pliku to alternatywne wersje treści, nie załączniki.

### PDF, DOC i MSG

- **PDF**: jedna część na stronę z pochodzeniem „strona N” (w mailu „załącznik: raport.pdf › strona 2”); liczba stron jest zapisywana w wersji. Strona z tekstem staje się częścią; strona z mniej niż `OCR_MIN_CHARS` (25) znakami innymi niż spacje, zawierająca obraz, to strona zeskanowana: zostaje jako część oznaczona `needs_ocr` (z tym niewielkim tekstem); pusta strona (bez tekstu i obrazu) jest pomijana.
- **Status skanów** (Faza 4): zeskanowane strony są rozpoznawane przez OCR w tym samym przebiegu (sekcja [OCR](#ocr)); wersja kończy się stanem `done` z `ocr_applied`. Stan `needs_ocr` („wymaga OCR”) zostaje tylko, gdy OCR jest wyłączony (`OCR_ENABLED=false`), PDF ma więcej zeskanowanych stron niż `OCR_MAX_PAGES` albo OCR się nie powiódł (limit czasu, awaria); strony z tekstem i tak są zapisane i wyszukiwalne, a „Przetwórz ponownie” próbuje jeszcze raz.
- **DOC**: LibreOffice konwertuje do DOCX, potem czyta go ekstraktor DOCX (tabele „A | B”, nagłówki, stopki).
- **MSG**: jak EML. Treść: tekstowa, w przeciwnym razie HTML zamieniany na tekst, w przeciwnym razie RTF (HTML osadzony w RTF przez extract-msg, zwykły RTF przez `rtf_text.py`). Załączniki zapisane w pliku przechodzą przez ten sam rejestr (typ po zawartości, pochodzenie „załącznik: nazwa”); załączone wiadomości Outlooka są przetwarzane jako wiadomości zagnieżdżone (każda to jeden poziom głębokości); inne rodzaje (obiekty OLE, odnośniki) są pomijane z adnotacją. Własne loggery bibliotek są wyciszone, więc nic z wiadomości nie trafia do logu. Bez wpisanego tytułu tytułem zostaje temat; strona dokumentu otwarta w trakcie przetwarzania pokazuje nowy tytuł zaraz po jego zakończeniu (odpytywanie statusu podmienia też nagłówek).

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

Faza 4, etap A (ADR-031). Dla każdego PDF, także PDF załączonego do maila (EML, MSG):
1. Tekst strona po stronie przez pypdfium2 (proces potomny). Strona „wymaga OCR”, gdy ma mniej niż `OCR_MIN_CHARS` znaków niebiałych (domyślnie 25) i zawiera obraz.
2. Jeśli jakaś strona wymaga OCR i OCR jest włączony, OCRmyPDF działa **tylko na tych stronach** (`--mode force --pages 2,5,…`) w procesie potomnym: języki `OCR_LANGUAGES` (`pol+eng`), automatyczny obrót stron położonych bokiem (`--rotate-pages`, pewność `OCR_ROTATE_THRESHOLD`, domyślnie 5), rasteryzacja stron przez pypdfium (`--rasterizer pypdfium`), **tylko tekst** (`--sidecar`, `--output-type none`): żaden plik pochodny nie jest zapisywany, „Pobierz oryginał” zwraca oryginał.
3. Rozpoznany tekst zastępuje części tych stron; dostają `ocr_applied` (wersja także); wersja kończy się stanem `done` („gotowe”). Strona, z której OCR wydobył mniej niż `OCR_MIN_CHARS` znaków, dostaje adnotację: „OCR nie znalazł (prawie) żadnego tekstu na stronach …” (pismo odręczne, obraz, bardzo słaby skan).
4. OCR nigdy nie kończy dokumentu błędem: jeśli jest wyłączony, PDF ma więcej zeskanowanych stron niż `OCR_MAX_PAGES` albo OCR się nie powiódł (limit czasu `OCR_TIMEOUT_SECONDS`, awaria), zeskanowane strony zostają oznaczone „wymaga OCR” z adnotacją w panelu tekstu, strony z tekstem są wyszukiwalne, a „Przetwórz ponownie” próbuje jeszcze raz.

Strona dokumentu pokazuje „Tekst: tekst z OCR” z ostrzeżeniem, że słowa, a zwłaszcza numery, mogą zawierać błędy rozpoznania; panel tekstu oznacza każdą rozpoznaną stronę „tekst z OCR”.

Wybory zmierzone na syntetycznych skanach (etap A): bez `--deskew` (jego binaryzacja ze stałym progiem zmieniała skan o niskim kontraście w pustą stronę; Tesseract sam czyta skos do około 5°, 8° nie działa w żadnym wariancie); próg obrotu 5 zamiast domyślnego 14 w OCRmyPDF (strona obrócona o 90° zostawała nieobrócona i wychodził bełkot); bez Ghostscripta (niepotrzebny przy rasteryzacji pypdfium i wyniku tylko tekstowym; nie jest instalowany). Izolacja, limity i brak tekstu z OCR w logach: zobacz [Limity bezpieczeństwa](#limity-bezpieczeństwa) i [Kod natywny w procesach potomnych](#kod-natywny-w-procesach-potomnych).

W dalszej części Fazy 4: etap B osobna kolejka i usługa dla OCR, automatyczny OCR do `OCR_AUTO_MAX_PAGES` (30) zeskanowanych stron i „Uruchom OCR” powyżej, stany OCR, `baza-wiedzy ocr --pending`; etap C etykieta w wynikach wyszukiwania i pomiary (czas na stronę, `tessdata_best`).

Znane ograniczenia do przekazania użytkownikom: jakość zależy od skanu; pismo odręczne nie jest obsługiwane; numery (NIP, numery kont i telefonów) mogą być źle odczytane; duże skany trwają minuty; PDF-y zabezpieczone hasłem kończą się komunikatem „PDF jest zabezpieczony hasłem”.

## Limity bezpieczeństwa

| Limit | Domyślnie | Ustawienie |
|---|---|---|
| Maksymalny rozmiar uploadu | 50 MB (proxy: `MAX_REQUEST_MB` 51) | `MAX_UPLOAD_MB` |
| Maksymalna głębokość załączników maila (EML, MSG, załączone wiadomości) | 3 | `INGEST_MAX_DEPTH` |
| Maksymalna liczba części (kawałków) na wersję | 2000 | `INGEST_MAX_PARTS` |
| Maksymalna ilość wyciągniętego tekstu na wersję | 50 MB (znaków) | `INGEST_MAX_TEXT_MB` |
| Maksymalna liczba stron PDF | 2000 | `INGEST_MAX_PAGES` |
| ZIP (DOCX): liczba wpisów, łączny rozmiar po rozpakowaniu, współczynnik wpisów powyżej 1 MB | 5000, 200 MB, 100× (sprawdzane z katalogu centralnego, zanim cokolwiek zostanie rozpakowane; dla DOCX wytworzonego przez LibreOffice z DOC tylko liczba wpisów i rozmiar) | stałe w `extractors/docx.py` |
| XML (DOCX) | bez wczytywania DTD, bez rozwijania encji (wewnętrzne „billion laughs” i zewnętrzne XXE), bez sieci, limit głębokości libxml2 (`huge_tree` wyłączone) | stałe |
| Proces potomny PDF: pamięć (przestrzeń adresowa), czas | 1024 MB, 300 s | `INGEST_CHILD_MEMORY_MB`, `INGEST_CHILD_TIMEOUT_SECONDS` |
| LibreOffice: pamięć (przestrzeń adresowa), czas, równoległe konwersje | 1024 MB, 120 s, jedna na kontener workera | `LIBREOFFICE_MEMORY_MB`, `LIBREOFFICE_TIMEOUT_SECONDS` |
| Limit czasu jednej próby | 900 s | `INGEST_TIME_LIMIT_SECONDS` |
| Próby (tylko błędy przejściowe) | 3, opóźnienie od 30 s, podwajane do 600 s | `INGEST_MAX_ATTEMPTS`, `INGEST_RETRY_MIN_SECONDS`, `INGEST_RETRY_MAX_SECONDS` |
| OCR (na PDF): zeskanowane strony, czas, czas na stronę, procesy Tesseracta, pamięć (przestrzeń adresowa) na proces, rozmiar obrazu strony | 200 stron, 600 s, 120 s, 2, 2048 MB, 200 megapikseli (więcej zeskanowanych stron: zostają „wymaga OCR” z adnotacją) | `OCR_MAX_PAGES`, `OCR_TIMEOUT_SECONDS`, `OCR_PAGE_TIMEOUT_SECONDS`, `OCR_JOBS`, `OCR_MEMORY_MB`, `OCR_MAX_IMAGE_MPIXELS` |
| Procesy workera (po jednym zadaniu), pamięć workera | 2, 2 GB | `WORKER_PROCESSES`, `WORKER_MEMORY_LIMIT` |

Pliki zabezpieczone hasłem kończą się komunikatem „…zabezpieczony hasłem. Wgraj kopię bez hasła.”: PDF, gdy pdfium żąda hasła, DOC po fladze szyfrowania (zanim wystartuje LibreOffice), DOCX już przy wgrywaniu (zaszyfrowany plik Office to kontener OLE2 z `EncryptionInfo`). Jako załączniki są pomijane z adnotacją.

Każda próba działa we własnym katalogu tymczasowym w `/data/storage/.work`, usuwanym po zakończeniu.

### Kod natywny w procesach potomnych

pdfium (PDF) i LibreOffice (DOC) to kod natywny, który na złośliwym lub uszkodzonym pliku może się wywrócić, zawiesić albo zajmować pamięć bez końca. Działają w procesie potomnym (`ingestion/sandbox.py`), nigdy w procesie workera:
- własna sesja i grupa procesów; po przekroczeniu czasu cała grupa dostaje SIGKILL (razem z procesami pomocniczymi LibreOffice), a grupa jest zabijana także po normalnym zakończeniu;
- limit przestrzeni adresowej (`RLIMIT_AS`) i brak zrzutów pamięci; sprawdzone: LibreOffice 25.2 konwertuje normalnie przy 1 GB, proces potomny pdfium kończy się czysto (`MemoryError` → „Przetworzenie pliku wymaga więcej pamięci…”);
- minimalne środowisko (bez zmiennych proxy i bazy), `HOME` i `TMPDIR` w katalogu próby, świeży profil LibreOffice (`-env:UserInstallation`) dla każdej konwersji, wynik tylko w plikach w tym katalogu (stdout i stderr idą do plików, nigdy do logu);
- jedna konwersja LibreOffice naraz na kontener workera (blokada pliku w `/data/storage/.work`); drugi proces workera czeka do `LIBREOFFICE_TIMEOUT_SECONDS`, potem ponawia później;
- brak sieci: worker należy tylko do wewnętrznej sieci Dockera `backend` (ADR-024, sprawdza to `make smoke`);
- awaria (sygnał) to trwały błąd „uszkodzony plik”, przekroczenie czasu to błąd przejściowy: ponawiany, a po ostatniej próbie „Przetwarzanie trwało dłużej niż limit czasu”.

Dodatkowo obowiązuje limit pamięci kontenera workera. Zmierzone 2026-11-17 na produkcyjnym pliku Compose z ustawieniami domyślnymi (2 procesy workera): w spoczynku 173 MiB; podczas przetwarzania 26 plików naraz (m.in. PDF 300 stron, DOC 1,5 MB z 3000 akapitów, TXT 10 MB i mail z dziesięcioma załącznikami PDF) najwyżej 457 MiB (szczyt cgroup z pamięcią podręczną stron 495 MiB), wszystko w 18 s; potem 339 MiB (Python zatrzymuje zwolnioną pamięć). `WORKER_MEMORY_LIMIT=2g` zostawia więc miejsce na jedną konwersję LibreOffice (≤ 1 GB) i bardzo duże teksty; przy każdym dodatkowym procesie workera zwiększ go o około 0,6 GB.

## Utrzymanie

- **Przetwórz ponownie**: przycisk „Przetwórz ponownie” pod wyodrębnionym tekstem (właściciel z `documents.upload` dla własnych dokumentów, `documents.edit_any` dla dowolnych) albo `baza-wiedzy reprocess <ID dokumentu lub wersji> …` / `baza-wiedzy reprocess --failed` w kontenerze `app`. Oba zerują licznik prób i trafiają do audytu (`document.reprocess`). W trakcie trwającej próby wersji nie da się przetworzyć ponownie („Dokument jest właśnie przetwarzany…”).
- **Zawieszone w „przetwarzanie”** (zabity worker): `baza-wiedzy reprocess --stuck` albo restart workera. Oczekiwany wynik: `Stuck versions: 1 queued again, 0 failed (no attempts left); waiting versions queued again: 0.`
- **Outbox zdarzeń**: `baza-wiedzy events redeliver` → `Events delivered: N, failed: 0, still undelivered: 0; old delivered entries removed: M.` Kod wyjścia 1, gdy handler zgłosił błąd (szczegóły w logu workera lub CLI: zdarzenie i klasa wyjątku).
- Po zmianie ekstraktorów lub konfiguracji wyszukiwania może być potrzebne `baza-wiedzy reindex-search` (zobacz [search.md](search.md), Faza 3) albo ponowne przetworzenie.
- Powód błędu widzi każdy, kto widzi dokument; szczegóły są w logu workera (`make logs`, szukaj identyfikatora wersji).

## Testy

Wszystkie pliki testowe są syntetyczne i generowane w testach (`tests/fixtures/samples.py`, `tests/fixtures/msg.py` na bazie `tests/fixtures/ole2.py`); w repozytorium nie ma żadnych binariów. Pliki DOC tworzy LibreOffice z HTML w obrazie testowym.

- Obraz testowy: etap `test` w `docker/worker/Dockerfile` (etap wydawany plus uv; nigdy nie jest publikowany: budowanie wydania i smoke biorą domyślny, ostatni etap `runtime`). `make test-db` uruchamia w nim znaczniki `integration` i `tools` (wymaga LibreOffice); `make test` na hoście pomija oba. Budowa korzysta z warstw systemowych workera: około minuty więcej przy pierwszej budowie w CI, sekundy z pamięcią podręczną; sam obraz workera buduje się w 138 s bez żadnej pamięci podręcznej.
- Jednostkowe: `tests/unit/test_ingestion_extractors.py` (TXT, podział, język, HTML, EML, DOCX) i `tests/unit/test_ingestion_formats.py`: BOM dla UTF-8 i UTF-16 LE/BE, długi tekst Windows-1250 kontra ISO-8859-2; PDF strona po stronie, strony skanowane i mieszane, puste strony, PDF zabezpieczony hasłem, uszkodzony i obcięty, limit stron; izolacja (przekroczenie czasu zabija grupę procesów, limit pamięci, minimalne środowisko); DOC przez LibreOffice (prywatny profil, brak pozostawionych procesów), zaszyfrowany DOC odrzucony przed LibreOffice, uszkodzony DOC, przekroczenie czasu LibreOffice, duży powtarzalny DOC; nagłówki MSG, treść tekstowa/HTML/RTF, załączniki DOCX/PDF/PNG, załączone wiadomości z własnymi załącznikami, limit głębokości, MSG w EML, uszkodzony MSG; RTF na tekst; billion laughs, głębokie zagnieżdżenie XML, zbyt wiele wpisów ZIP; zaszyfrowany DOCX przy wgrywaniu i jako załącznik.
- Integracyjne (`tests/integration/test_ingestion.py`, prawdziwy worker na Redis): TXT i EML z DOCX; EML z załącznikami PDF i MSG; MSG z tematem jako tytułem i metadanymi maila; DOC; zeskanowany PDF → „wymaga OCR”; zaszyfrowany PDF → czytelny błąd bez ponawiania; przekroczenia czasu procesów potomnych ponawiane i zgłaszane; logi PDF, MSG i uszkodzonych plików bez treści; plus wszystko z etapu B.
- `make smoke` wgrywa przez proxy mały plik TXT i mały PDF z tekstem i czeka na „gotowe”.
- `make probki DIR=<katalog>` zapisuje po jednej próbce każdego przypadku do ręcznego testu, z oczekiwanymi wynikami w `OCZEKIWANE.txt`.
