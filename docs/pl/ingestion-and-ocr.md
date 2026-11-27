# Przetwarzanie dokumentów i OCR

> Dotyczy: v0.5.0 · Ostatnia aktualizacja: 2026-11-27

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
| PDF skan | strony bez warstwy tekstu są rozpoznawane przez OCR (OCRmyPDF + Tesseract, `pol+eng`) w usłudze `ocr-worker`, także w załącznikach maili; tylko tekst | Faza 4 |

Załączniki EML/MSG, które same są obsługiwanymi plikami, są wyciągane rekurencyjnie (głębokość ≤ `INGEST_MAX_DEPTH`, domyślnie 3) i zapisywane jako części typu `attachment` (albo `page` dla stron PDF) w `ing_parts`; każda załączona wiadomość Outlooka to jeden poziom. Pozostałe załączniki (obrazy, archiwa) oraz zabezpieczone hasłem są pomijane z adnotacją na stronie dokumentu. OCR samodzielnych obrazów jest poza zakresem MVP.

## Walidacja uploadu (w `app`)

Strona „Moje dokumenty” → „Wgraj dokumenty” (`/documents/upload`, uprawnienie `documents.upload`). Jeden formularz przyjmuje kilka plików (ADR-032); każdy plik przechodzi poniższe kroki **osobno** (własna transakcja, wpis audytu i zdarzenie), więc odrzucony albo wadliwy plik nigdy nie zatrzymuje pozostałych. Formularz to zwykły POST multipart i działa bez JavaScriptu: token CSRF jest jego **pierwszym** polem, które middleware CSRF czyta z początku ciała (pliki płyną dalej nietknięte); HTMX wysyła go także w nagłówku `X-CSRF-Token`. Mały skrypt (`static/js/upload.js`) dodaje przeciąganie i sprawdza limity przed wysłaniem.

Limity jednego formularza: najwyżej `UPLOAD_MAX_FILES` plików (domyślnie 50; kolejne są odrzucane pojedynczo, ponad 50 powyżej limitu odrzuca cały formularz) i razem `UPLOAD_MAX_TOTAL_MB` (domyślnie 500, najwyżej `MAX_REQUEST_MB` − 1; większy `Content-Length` odrzuca formularz komunikatem „Pliki są razem większe niż dozwolony rozmiar (… MB). Wgraj je w kilku częściach.”). Proxy ucina ciała większe niż `MAX_REQUEST_MB` (domyślnie 501) i odpowiada polską stroną zamiast pustego błędu. Starlette buforuje pliki w katalogu tymczasowym kontenera app: zapewnij tam `UPLOAD_MAX_TOTAL_MB` wolnego miejsca.

Wyniki: jeden plik wgrany → strona dokumentu, jak dotąd; jeden plik odrzucony → formularz z komunikatem, jak dotąd; w pozostałych przypadkach przeglądarka przechodzi do „Moje dokumenty” z tabelą „Wynik wgrywania” (dla każdego pliku: wgrano, możliwy duplikat, pominięto, odrzucono z powodem) i podsumowaniem. Tabela to mały plik JSON `/data/storage/.batches/<losowy id>.json`, czytelny tylko dla wgrywającego, trzymany `UPLOAD_BATCH_HOURS` (domyślnie 24) i usuwany przy późniejszym wgraniu.

1. Rozmiar: żądanie jest odrzucane wcześnie, gdy `Content-Length` przekracza `MAX_UPLOAD_MB` (domyślnie 50), a kopiowanie przerywa się, gdy tylko faktyczny rozmiar go przekroczy (HTTP 413, „Plik jest większy niż dozwolony rozmiar.”; przy kilku plikach: ten plik jest odrzucany, pozostałe idą dalej).
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
6. Duplikaty: gdy inny dokument **widoczny dla wgrywającego** ma wersję o tym samym SHA-256, plik nie jest jeszcze zapisywany, tylko **wstrzymany** (`/data/storage/.batches/<id>/<n>.part`, ten sam wolumen) jako „możliwy duplikat” z odnośnikami do tych dokumentów; wgrywający wybiera „Wgraj mimo to” (wstrzymany plik jest zapisywany i wykonują się kroki 4, 5, 7; strona dokumentu pokazuje potem „Możliwy duplikat” jak dotąd) albo „Pomiń” (wstrzymany plik jest usuwany). Każda decyzja to POST na `/documents/upload/batches/<id>/<n>` (CSRF, uprawnienie `documents.upload`, tylko własna partia wgrywającego; druga decyzja niczego nie zmienia). Cudze szkice nigdy nie są porównywane, więc wstrzymanie niczego nie zdradza.
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
- **Status skanów** (Faza 4): przebieg tekstowy kończy się stanem `needs_ocr` („wymaga OCR”) z oznaczonymi zeskanowanymi stronami i stanem OCR; przebieg OCR w `ocr-worker` je rozpoznaje i przenosi wersję w `done` z `ocr_applied` (sekcja [OCR](#ocr)). Strony z tekstem i tak są zapisane i wyszukiwalne.
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

Faza 4 (ADR-031 silnik, ADR-033 kolejka). Dwa przebiegi na wersję, w dwóch usługach:

1. **Przebieg tekstowy** (usługa `worker`, kolejka `ingestion`, nigdy nie uruchamia OCR): tekst strona po stronie przez pypdfium2; strona „wymaga OCR”, gdy ma mniej niż `OCR_MIN_CHARS` znaków niebiałych (domyślnie 25) i zawiera obraz. Zeskanowane strony są zapisywane jako części oznaczone „wymaga OCR”; strony z tekstem są od razu wyszukiwalne. Potem wersja dostaje stan OCR zależny od liczby zeskanowanych stron (łącznie z załącznikami):

   | Zeskanowane strony | Stan OCR | Widoczne jako |
   |---|---|---|
   | 0 | `none` | „gotowe” |
   | 1 … `OCR_AUTO_MAX_PAGES` (30) | `queued`: wiadomość OCR wysyłana od razu | „wymaga OCR” + „OCR w kolejce” |
   | powyżej `OCR_AUTO_MAX_PAGES` do `OCR_MAX_PAGES` (200) | `on_hold` | „OCR wstrzymany (duży dokument)” + przycisk „Uruchom OCR” |
   | powyżej `OCR_MAX_PAGES` | `skipped` | „OCR pominięty (za dużo stron)”; skanów nie da się wyszukać; uruchomić może tylko operator: `baza-wiedzy ocr <ID> --ignore-page-limit` (audyt, `source: cli_override`, bez przycisku) |
   | dowolnie, `OCR_ENABLED=false` | `none` | „OCR jest wyłączony na tym serwerze” |

2. **Przebieg OCR** (usługa `ocr-worker`, kolejka `ocr`, jeden dokument naraz): każda wiadomość bierze „następną wersję w kolejce”, tę z **najmniejszą liczbą zeskanowanych stron** (potem najstarsze zgłoszenie) przez `SELECT … FOR UPDATE SKIP LOCKED`, więc krótkie dokumenty nigdy nie czekają za długim. Kolejność jest w bazie: priorytet wiadomości Dramatiq zmieniałby tylko kolejność wiadomości już pobranych przez workera z Redisa. Wersja przechodzi w `running` („OCR w toku”); cały plik jest wyciągany ponownie z włączonym OCR (skany mogą być w załącznikach maili): OCRmyPDF **tylko na oznaczonych stronach** (`--mode force --pages 2,5,…`) w procesie potomnym, języki `OCR_LANGUAGES` (`pol+eng`), obrót stron położonych bokiem (`--rotate-pages`, pewność `OCR_ROTATE_THRESHOLD`, domyślnie 5), rasteryzacja przez pypdfium, **tylko tekst** (`--sidecar`, `--output-type none`: bez pliku pochodnego; „Pobierz oryginał” zwraca oryginał). Nowe części zastępują stare w jednej transakcji z `document.version.extracted` w outboksie (wyszukiwarka bierze tekst); wersja przechodzi w `done` („gotowe”, stan OCR `done`, `ocr_applied`).
3. **Limity**: `OCR_TIMEOUT_SECONDS` (domyślnie 1800) na **cały dokument**, wspólny dla wszystkich jego PDF-ów; `OCR_PAGE_TIMEOUT_SECONDS` (120) na stronę w Tesseract; `OCR_JOBS` (2) procesy Tesseracta po jednym wątku (`OMP_THREAD_LIMIT=1`); `OCR_MEMORY_MB` (2048) przestrzeni adresowej na proces (RLIMIT_AS); `OCR_MAX_IMAGE_MPIXELS` (200); limity kontenera `OCR_MEMORY_LIMIT` (2g) i `OCR_CPUS` (2). Po przekroczeniu limitu czasu grupa procesów jest zabijana, a wersja pokazuje „OCR nieudany” z komunikatem „OCR trwał dłużej niż limit czasu…”; awaria daje „OCR się nie powiódł…”. Poprzednie części zostają (strony z tekstem są wyszukiwalne), dokument nigdy nie kończy się błędem z powodu OCR, a „Uruchom OCR ponownie”, „Przetwórz ponownie” albo `baza-wiedzy ocr <ID>` próbuje jeszcze raz. Worker OCR zabity w trakcie (pamięć) zostawia stan `running`; następny start workera oznacza go jako nieudany („OCR został przerwany…”) po `OCR_TIMEOUT_SECONDS` + 10 minut i nie ponawia go sam.
4. **Anulowanie**: „Anuluj OCR” (w kolejce albo w toku) ustawia „OCR wstrzymany”; działający Tesseract jest zabijany w ciągu 2 sekund (worker OCR sprawdza stan), a jego wynik odrzucany. „Uruchom OCR” uruchamia go ponownie.
5. **Kto**: „Uruchom OCR”, „Uruchom OCR ponownie” i „Anuluj OCR” — właściciel z `documents.upload` (własne dokumenty) i `documents.edit_any` (każdy widoczny dokument), jak „Przetwórz ponownie”; audyt `document.ocr_request` (`version_id`, `pages`, `source`: `button`, `cli`, `cli_override` dla `--ignore-page-limit`, `relevance` dla `make relevance OCR=1` na stosie deweloperskim) i `document.ocr_cancel`. Automatyczne uruchomienie nie jest audytowane (nikt o nie nie prosił).
6. **Wersje sprzed 0.5.0** („wymaga OCR”, stan OCR `none`, widoczne jako „OCR nieuruchomiony”) **nie** są uruchamiane przez aktualizację: `baza-wiedzy ocr --pending [--max-pages N]` uruchamia je ręcznie (także wstrzymane), `baza-wiedzy ocr <ID dokumentu lub wersji>` jeden dokument. Zobacz [deployment-operations.md](deployment-operations.md#przetwarzanie-dokumentów-przewodnik-operatora).

Strona, z której OCR nie wydobył ani jednego znaku, dostaje adnotację „OCR nie znalazł żadnego tekstu na stronie …”; strona z mniej niż `OCR_MIN_CHARS` znakami „OCR znalazł bardzo mało tekstu: strona 2 (3 znaki)” (pismo odręczne, obraz, bardzo słaby skan). Komunikaty z liczbą używają polskich form liczby mnogiej (1 / 2–4 / 5+).

Strona dokumentu pokazuje „Tekst: tekst z OCR” z ostrzeżeniem, że słowa, a zwłaszcza numery, mogą zawierać błędy rozpoznania; panel tekstu oznacza każdą rozpoznaną stronę „tekst z OCR”.

Wybory zmierzone na syntetycznych skanach (etap A, sprawdzone ponownie w etapie C, ADR-034): bez `--deskew`, próg obrotu 5, bez Ghostscripta (niepotrzebny przy rasteryzacji pypdfium i wyniku tylko tekstowym; nie jest instalowany). Ani rozpoznany tekst, ani komunikaty narzędzi OCR nie trafiają do żadnego logu: logi zawierają identyfikatory, liczby stron, czasy i powody (`ocr: done version=… pages=… recognised=… left=… seconds=…`).

- **`--deskew`** (trafność słów na stronie z 10 wierszami, bez / z): skos 2° 100 / 100 %, 3° 98,4 / 100 %, 4° 100 / 100 %, 5° 95,3 / 98,4 %, 6° 65,6 / 65,6 %, 7° 31,2 / 31,2 %, 8° 0 / 0 %; niski kontrast 140/200 i 120/210 96,9 / 100 %. Ale na krótkiej stronie (4 wiersze) z niskim kontrastem 140/200 `--deskew` daje **pustą stronę** (100 % → 0 %): binaryzacja ze stałym progiem gubi blady tekst. Zysk 1–4 pkt nie równoważy utraty całych stron, a powyżej 5° nie pomaga wcale, więc zostaje wyłączony. Strony przekrzywione o więcej niż około 5° trzeba zeskanować ponownie.
- **Próg obrotu 5**: strona obrócona o 90° jest wykrywana z pewnością około 6,3 i obracana; strony prawidłowo ułożone (czysta, mały druk, skos 3°) są „ku górze” (pewność około 6,9) i nigdy nie są obracane; strony z bardzo małą ilością tekstu (jeden wiersz, tabela) nie dają żadnej decyzji i zostają bez zmian. Testy w `tests/unit/test_ocr.py`.
- **Wyszukiwanie w tekście z OCR** (`tests/search_golden/test_ocr_golden.py`): na wszystkich 11 syntetycznych skanach z wadami znajdowane są polskie odmiany, słowa bez polskich znaków, formy angielskie i frazy. Granicą są numery: telefon i NIP są znajdowane na każdym skanie poza tym z 6 % szumu, gdzie OCR źle odczytuje cyfry. Użytkownikom warto mówić, by w skanach szukali raczej słów niż numerów, a numery sprawdzali w oryginale.

**Pomiary** (`make perf-ocr PAGES=50 BEST=1`, komputer deweloperski, kontener ograniczony jak `ocr-worker`: 2 CPU, 2 GB, `OCR_JOBS=2`; syntetyczne strony A4 w 200 dpi ze wszystkimi wadami; wolniejszy procesor serwera wydłuża czasy proporcjonalnie):

| | Debian „fast” (w obrazie) | tessdata_best 4.1.0 (wariant) |
|---|---|---|
| Jeden dokument 50 stron | 50 s, **60 stron/min** | 52 s, 57 stron/min |
| 22 dokumenty po 1–5 stron | p50 2,5 s, **p95 5,4 s**, maks. 6,3 s, 44 strony/min | p50 2,9 s, p95 6,4 s, maks. 6,8 s, 38 stron/min |
| Trafność słów (średnio) | 98,0 % | 99,1 % |
| … szum 4 % | 96,9 % | 93,8 % |
| … mały druk, niski kontrast | 96,9 % | 100 % |
| Największy proces / szczyt wszystkich procesów OCR | 281 MB / **468 MB** | 273 MB / 486 MB |

Decyzja (ADR-034): zostają modele „fast”. „best” zyskuje około jednego punktu trafności słów, traci na zaszumionych skanach, jest o 10–20 % wolniejszy na dokument i dodałby 27 MB modeli spoza pakietów Debiana; przy wyszukiwaniu odmian pozostałe pomyłki rzadko mają znaczenie. Do ponownej oceny na prawdziwych skanach, jeśli `make relevance` pokaże słowa gubione w tekście z OCR.

Rekomendacja dla 4 vCPU / 8 GB (wartości domyślne): `OCR_CPUS=2`, `OCR_JOBS=2` (jeden proces Tesseracta na rdzeń), `OCR_MEMORY_LIMIT=2g` (cztery razy zmierzony szczyt, zapas na skany A3 lub 300 dpi), `OCR_MEMORY_MB=2048`, `OCR_AUTO_MAX_PAGES=30` (około 30 s OCR), `OCR_TIMEOUT_SECONDS=1800` (dokument 200-stronicowy trwa tu około 4 minut; limit zostawia zapas na serwer 5× wolniejszy). Przy 8 vCPU: `OCR_CPUS=4`, `OCR_JOBS=4`, `OCR_MEMORY_LIMIT=3g`.

Znane ograniczenia do przekazania użytkownikom: jakość zależy od skanu; pismo odręczne nie jest obsługiwane; numery (NIP, numery kont i telefonów) mogą być źle odczytane; duże skany trwają minuty; PDF-y zabezpieczone hasłem kończą się komunikatem „PDF jest zabezpieczony hasłem”.

## Limity bezpieczeństwa

| Limit | Domyślnie | Ustawienie |
|---|---|---|
| Maksymalny rozmiar pliku; pliki i łączny rozmiar jednego formularza | 50 MB; 50 plików, 500 MB (proxy: `MAX_REQUEST_MB` 501) | `MAX_UPLOAD_MB`; `UPLOAD_MAX_FILES`, `UPLOAD_MAX_TOTAL_MB` |
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
| OCR: zeskanowane strony uruchamiane automatycznie / najwyżej, czas na dokument, czas na stronę, procesy Tesseracta, pamięć (przestrzeń adresowa) na proces, rozmiar obrazu strony | 30 / 200 stron, 1800 s, 120 s, 2, 2048 MB, 200 megapikseli; kontener `ocr-worker`: 2 GB, 2 CPU | `OCR_AUTO_MAX_PAGES` / `OCR_MAX_PAGES`, `OCR_TIMEOUT_SECONDS`, `OCR_PAGE_TIMEOUT_SECONDS`, `OCR_JOBS`, `OCR_MEMORY_MB`, `OCR_MAX_IMAGE_MPIXELS`; `OCR_MEMORY_LIMIT`, `OCR_CPUS` |
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
- OCR (`make test-db`, Tesseract w obrazie testowym): `tests/unit/test_ocr.py` (wiersz poleceń, odczyt pliku tekstowego, adnotacje, decyzje o obrocie: strony prawidłowo ułożone nigdy nie są obracane, strona położona bokiem jest obracana), `tests/integration/test_ocr_queue.py` (stany, najkrótszy najpierw, wstrzymanie i „Uruchom OCR”, anulowanie, limit czasu, przerwany OCR, `ocr --pending`, `--ignore-page-limit`, audyt), `tests/search_golden/test_ocr_golden.py` (wyszukiwanie w tekście 11 syntetycznych skanów z wadami, etykieta „tekst z OCR”, ranking względem warstwy tekstu).
- `make smoke` wgrywa przez proxy mały plik TXT, mały PDF z tekstem i jednostronicowy skan i czeka na „gotowe” (słowo ze skanu jest znajdowane z etykietą „tekst z OCR”, OCR działał w `ocr-worker`, jego tekstu nie ma w żadnym logu), potem skan powyżej `OCR_AUTO_MAX_PAGES` czeka wstrzymany do „Uruchom OCR”.
- `make probki DIR=<katalog>` zapisuje po jednej próbce każdego przypadku do ręcznego testu, z oczekiwanymi wynikami w `OCZEKIWANE.txt`.
