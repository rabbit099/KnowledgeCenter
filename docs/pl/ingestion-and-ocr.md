# Przetwarzanie dokumentów i OCR

> Dotyczy: v0.0.0 (przed pierwszym wydaniem) · Ostatnia aktualizacja: 2026-11-12

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

1. Rozmiar ≤ `MAX_UPLOAD_MB` (domyślnie 100); proxy wymusza ten sam limit.
2. Rozszerzenie z listy dozwolonych **oraz** typ wykryty przez libmagic zgadza się z rozszerzeniem.
3. SHA-256 liczone w trakcie zapisu strumieniowego na dysk.
4. Zapis pod kluczem UUID; nazwa pliku użytkownika jest tylko metadaną i nigdy nie służy jako ścieżka.
5. Jeśli taki SHA-256 już istnieje, użytkownik dostaje ostrzeżenie (bez blokady).
6. Opcjonalne skanowanie antywirusowe ClamAV, gdy włączony jest profil `antivirus`.
7. Tworzone są wiersze w bazie, publikowane jest zdarzenie `document.version.uploaded` (`ingestion` kolejkuje zadanie), zapisywany jest wpis audytowy.

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
| Maksymalny rozmiar uploadu | 100 MB | `MAX_UPLOAD_MB` |
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
