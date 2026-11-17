# Przetwarzanie dokumentów i OCR

> Dotyczy: v0.2.1 · Ostatnia aktualizacja: 2026-11-17

Jak wgrany plik staje się przeszukiwalnym tekstem. Walidacja uploadu i zapis plików należą do modułu `documents`; opisane tu przetwarzanie należy do modułu `ingestion` (zobacz [modules/ingestion.md](modules/ingestion.md)).

## Obsługiwane formaty

| Format | Ekstraktor | Uwagi |
|---|---|---|
| PDF z tekstem | pypdfium2, strona po stronie | Jeden wiersz `srch_parts` na stronę |
| PDF skan | OCRmyPDF + Tesseract (`pol+eng`) | OCR tylko dla stron bez tekstu |
| DOCX | python-docx (akapity, tabele, nagłówki, stopki) + zapasowo surowy XML | Pola tekstowe i przypisy czytane z XML |
| DOC | Konwersja LibreOffice headless do tekstu | Izolowany profil, timeout |
| TXT | charset-normalizer, potem UTF-8 | Obsługuje UTF-8, Windows-1250, ISO-8859-2 |
| EML | `email` ze standardowej biblioteki | Nagłówki, treść tekstowa (treść HTML zamieniana na tekst), załączniki |
| MSG | `extract-msg` | Ten sam wynik co dla EML |

Załączniki EML/MSG będące PDF, DOCX, DOC, TXT, EML lub MSG są wyciągane rekurencyjnie (głębokość ≤ 3) i zapisywane jako `srch_parts` typu `attachment`. Pozostałe załączniki (obrazy, archiwa) są tylko wymieniane z nazwy; ich nazwy plików pozostają przeszukiwalne. OCR samodzielnych obrazów jest poza zakresem MVP.

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
5. Powstaje dokument-szkic z wersją 1 (`doc_documents`, `doc_versions`, `processing_status = pending`); tytuł = opcjonalne pole „Tytuł” albo nazwa pliku bez rozszerzenia.
6. Duplikaty: strona dokumentu pokazuje „Możliwy duplikat” z odnośnikami, gdy inny dokument **widoczny dla wgrywającego** ma wersję o tym samym SHA-256. Cudze szkice nigdy nie są porównywane, więc ostrzeżenie niczego nie zdradza. Wgranie nie jest blokowane; usuwanie duplikatu dojdzie z cyklem życia (Faza 5).
7. Audyt `document.upload` z identyfikatorem dokumentu, `version_id`, `sha256`, `size_bytes` i `kind` (bez nazwy pliku i treści). Po zatwierdzeniu publikowane jest zdarzenie `document.version.uploaded` (`ingestion` kolejkuje przetwarzanie, etap B).
8. Skanowanie antywirusowe: krok skanowania w pipeline przetwarzania, domyślnie wyłączony (etap B); ClamAV nie jest podłączany w Fazie 2.

Szkice widzi tylko wgrywający i posiadacze `documents.read_drafts` (Admin); każdy inny dostaje **404** na stronie dokumentu, więc jego istnienie nie jest ujawniane.

## Przetwarzanie (w `worker`)

```
pending → processing → done
                    ↘ failed (z processing_error)
```

`done` może mieć `processing_warnings` (na przykład jednego załącznika nie udało się odczytać).

Kroki:
1. Rozpoznaj rodzaj pliku po zawartości, a nie po rozszerzeniu.
2. Uruchom ekstraktor. Wynikiem jest lista części: tekst, numer strony, nazwa, rodzic.
3. Podziel tekst dłuższy niż 100 000 znaków.
4. Wykryj język każdej części (polski/angielski przez lingua; krótki tekst → `unknown`).
5. Opublikuj zdarzenie `document.version.extracted` z częściami i metadanymi; moduł `search` zapisuje je w `srch_parts` (baza liczy `tsv_pl`, `tsv_en`, `tsv_simple`).
6. Dla EML/MSG wypełnij `doc_email_metadata` przez `documents.services.api`.
7. Ustaw `processing_status = done` przez `documents.services.api` i zapisz w logu (niezależnie od audytu) czas trwania i liczby elementów.

Zadania są idempotentne: ponowne przetworzenie publikuje zdarzenie jeszcze raz, a `search` zastępuje istniejące części wersji. Ponowienia: do 3 z narastającą przerwą dla błędów przejściowych (baza, Redis, timeouty); błędy trwałe kończą się od razu czytelnym komunikatem.

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
| Maksymalna głębokość załączników maila | 3 | `MAX_ATTACHMENT_DEPTH` |
| Maksymalna ilość wyciągniętego tekstu na wersję | 50 MB | `MAX_EXTRACTED_TEXT_MB` |
| Współczynnik rozpakowania ZIP (DOCX) | 100× | `MAX_ZIP_RATIO` |
| Timeout LibreOffice | 120 s | `LIBREOFFICE_TIMEOUT_SECONDS` |
| Timeout OCR | 1800 s | `OCR_TIMEOUT_SECONDS` |
| Współbieżność workera | 2 | `WORKER_CONCURRENCY` |

Każde zadanie działa we własnym katalogu tymczasowym w `/data/tmp`, usuwanym po zakończeniu. Kontener workera ma limit pamięci.

## Utrzymanie

- Zawieszoną lub nieudaną wersję można przetworzyć ponownie z panelu admina albo poleceniem `baza-wiedzy reprocess <version_id>`.
- Po zmianie ekstraktorów lub konfiguracji wyszukiwania może być potrzebne `baza-wiedzy reindex-search` (zobacz [search.md](search.md)) albo `baza-wiedzy reprocess --all`.
- Powody niepowodzeń widzi osoba wgrywająca i administratorzy.

## Testy

Fixture'y (syntetyczne) dla: PDF z tekstem, PDF skan (sam obraz), PDF mieszany, PDF zaszyfrowany, DOCX z tabelą i polem tekstowym, DOC, TXT w UTF-8 i Windows-1250, EML z załącznikiem PDF, EML z treścią tylko w HTML, MSG z załącznikiem, EML zagnieżdżony w EML, DOCX przypominający zip-bombę (musi zostać odrzucony).
