# Moduł: ingestion

> Dotyczy: v0.3.0 · Ostatnia aktualizacja: 2026-11-18

## Zakres

Zamienia zapisany plik na części tekstu w `worker`: uruchamia ekstraktor dla typu wersji (TXT, EML, DOCX, PDF, DOC, MSG; kod natywny w procesach potomnych; OCR w Fazie 4), przetwarza załączniki maili, dzieli długi tekst, wykrywa język każdej części, pilnuje limitów bezpieczeństwa, prowadzi status przetwarzania (ponawianie, odzyskiwanie zawieszonych) i publikuje `document.version.extracted` przez swój outbox. Szczegóły: [ingestion-and-ocr.md](../ingestion-and-ocr.md).

## Zależności

Wymaga [documents](documents.md) (status, metadane maila, widoczność, nazwy plików i sprawdzanie typu przez `documents.services.api`). Biblioteki parserów (`python-docx`, `pypdfium2`, `extract-msg`, `lingua-language-detector`, OCRmyPDF) i LibreOffice należą do dodatku `worker` i są tylko w obrazie workera; aplikacja webowa importuje wyłącznie lekkie części (trasy, `tasks.enqueue`, `services/api.py`), co sprawdza test jednostkowy.

## Uprawnienia

Brak własnych. „Przetwórz ponownie” (`POST /ingestion/versions/<id>/reprocess`) może wykonać właściciel z `documents.upload` (własne dokumenty) i posiadacz `documents.edit_any` (dowolne), i tylko dla dokumentów, które użytkownik widzi (w przeciwnym razie 404, a 403, gdy widzi, ale nie ma prawa). Strona części: `GET /ingestion/versions/<id>/parts` (fragment HTMX na stronie dokumentu), każdy, kto widzi dokument. Audyt: `document.reprocess` (identyfikator dokumentu, `version_id`, `sha256`).

## Tabele

Gałąź `ingestion` (`ingestion_0001`, `depends_on` `documents_0002`):
- `ing_parts`: wyciągnięty tekst, jeden wiersz na kawałek (ADR-020); `version_id` → `doc_versions` z `ON DELETE CASCADE`.
- `ing_results`: dla każdej wersji adnotacje (wykryte kodowanie, pominięte załączniki, odczyt zapasowy), liczba części i znaków.
- `ing_outbox`: trwałe zdarzenia `document.version.extracted`.

Kolumny: [data-model.md](../data-model.md#ingestion).

## Zdarzenia

- Subskrybuje `document.version.uploaded` (w `app`): kolejkuje `ingestion.process_version`. Gdy Redis jest niedostępny, wersja zostaje w stanie oczekiwania i kolejkuje ją start workera.
- Publikuje `document.version.extracted` (`document_id`, `version_id`; bez tekstu) przez outbox, co najmniej raz, po zatwierdzeniu części. Subskrybenci czytają części przez `parts_of` i muszą być idempotentni.

## Interfejs publiczny

`modules/ingestion/services/api.py`:
- `parts_of(db, version_id)` → lista `PartView` (kolejność, typ części, pochodzenie, strona, numer kawałka, język, tekst, rozmiar, flaga OCR) — źródło dla indeksu wyszukiwania (Faza 3).
- `notes_of(db, version_id)`, `extracted_version_ids(db)` (pełna reindeksacja), `text_part_counts(db, version_ids=None)` (wyodrębnione wersje → liczba części z tekstem; `search-check`).
- `request_reprocess(db, settings, version_id, actor=…, ip=…)`: powrót do oczekiwania z wyzerowanym licznikiem prób i audytem; wywołujący sprawdza uprawnienia, zatwierdza i kolejkuje; zgłasza `ReprocessBusy`, gdy trwa próba.

Hook startu workera (manifest `worker_startup`): `startup.on_worker_start` (pliki tymczasowe, zawieszone i oczekujące wersje, outbox). CLI: `baza-wiedzy reprocess`, `baza-wiedzy events redeliver` ([deployment-operations.md](../deployment-operations.md)).

## Punkty rozszerzeń

- Rejestr ekstraktorów (`extractors/__init__.py`): nowy format to nowy plik z `extract(path, ctx)` i jeden wiersz w rejestrze. `ctx.add_text()`, `ctx.note()` i `ctx.attachment()` dają za darmo limity, pochodzenie i rekurencję.
- Hook skanowania antywirusowego (`scanning.py`, `SCAN_HOOK`).

## Status

Faza 2, etapy B i C: pipeline (przejęcie, hook skanowania, ekstrakcja, podział, język, zapis w jednej transakcji, outbox), statusy z ponawianiem błędów przejściowych i odzyskiwaniem zawieszonych, ekstraktory TXT, EML, DOCX, PDF (proces potomny, zeskanowane strony oznaczane „wymaga OCR”), DOC (LibreOffice w procesie potomnym) i MSG, limity przeciw bombom ZIP i XML, strona części i „Przetwórz ponownie” (UI i CLI), temat maila jako domyślny tytuł. Worker nie ma dostępu do internetu (ADR-024). Faza 3: wyszukiwarka czyta części; Faza 4: OCR.
