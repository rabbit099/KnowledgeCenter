# Baza Wiedzy — Dokumentacja (polski)

> Dotyczy: v0.5.0 · Ostatnia aktualizacja: 2026-11-27

Baza Wiedzy (nazwa robocza) to wewnętrzna aplikacja webowa. Użytkownicy wgrywają dokumenty (PDF, DOCX, DOC, TXT, EML, MSG), system odczytuje ich tekst (OCR dla skanów) i wyszukuje dokumenty zawierające wpisaną frazę. Działa w Dockerze na serwerze z Linuksem.

Angielska wersja tej dokumentacji: [`../en/`](../en/README.md).

## Spis treści

| Dokument | Czego dotyczy |
|---|---|
| [architecture.md](architecture.md) | Usługi, przepływ danych, stack technologiczny |
| [data-model.md](data-model.md) | Tabele bazy danych, relacje, indeksy |
| [search.md](search.md) | Jak działa wyszukiwanie: indeksacja polska i angielska, ranking, fragmenty |
| [user-guide.md](user-guide.md) | Wyszukiwanie dla użytkowników i administratora: składnia, odmiana, numery i kody, ograniczenia, widoczność, prywatność, polecenia operatora |
| [ingestion-and-ocr.md](ingestion-and-ocr.md) | Przetwarzanie uploadu, wyciąganie tekstu z każdego formatu, OCR |
| [auth-and-permissions.md](auth-and-permissions.md) | Logowanie przez Active Directory, role, katalog uprawnień, audyt |
| [deployment-operations.md](deployment-operations.md) | Instalacja, konfiguracja, aktualizacje, rollback, backup, monitoring |
| [modules/documents.md](modules/documents.md), [modules/ingestion.md](modules/ingestion.md), [modules/search.md](modules/search.md) | Dla każdego modułu: zakres, zależności, uprawnienia, tabele, zdarzenia, punkty rozszerzeń |
| [decisions.md](decisions.md) | Rejestr decyzji (ADR), założenia i otwarte pytania |
| [changelog.md](changelog.md) | Co zmieniło się w każdej wersji |

## Plan prac

Zaznaczaj punkty dopiero wtedy, gdy są skończone i opisane w dokumentacji.

### Faza 0 — Fundament
- [x] Repozytorium, `pyproject.toml`, Makefile, pre-commit
- [x] Rejestr modułów: manifesty, `ENABLED_MODULES`, magistrala zdarzeń, kontrola granic (import-linter)
- [x] Docker Compose: proxy, app, worker, db, redis (+ opcjonalny profil ClamAV)
- [x] Własny obraz Postgresa ze słownikiem Hunspell dla polskiego; wynik spike'a opisany w `search.md`
- [x] Baza Alembica, `/health` i `/ready`
- [x] CI (lint, typy, testy, docs-check, budowanie obrazów, test dymny); workflow wydania użyty dla v0.1.0
- [x] `scripts/check_docs.py`

### Faza 1 — Użytkownicy i uprawnienia
- [x] Lokalny administrator awaryjny (CLI), sesje, CSRF, limit prób logowania
- [x] Logowanie przez Active Directory, mapowanie grup na role
- [x] Role i katalog uprawnień, panel admina dla użytkowników i ról
- [x] Log audytowy i jego przeglądarka

### Faza 2 — Upload i przetwarzanie
- [x] Upload z walidacją, zapis plików, SHA-256, ostrzeżenie o duplikacie
- [x] Ekstraktory: PDF (tekst), DOCX, DOC, TXT, EML, MSG (+ załączniki)
- [x] Pipeline w workerze, statusy, ponowienia, akcja „przetwórz ponownie”
- [x] Wykrywanie języka dla każdej części

### Faza 3 — Wyszukiwanie
- [x] Publikacja, cofnięcie publikacji, archiwizacja, przywrócenie (przeniesione z Fazy 5; ADR-025), reguła ról „publish_any i edit_any wymagają read_drafts”
- [x] Pobieranie oryginału z bezpiecznymi nagłówkami, z audytem
- [x] Żadnych wyszukiwanych fraz, nazw plików ani tokenów w logach (proxy, aplikacja, błędy bazy)
- [x] Syntetyczny polsko-angielski korpus wyszukiwania dla testów golden i `make probki`
- [x] Indeksacja w `pl`, `english`, `simple_unaccent`, identyfikatory jako dodatkowe tokeny (ADR-026)
- [x] Interfejs wyszukiwania (zwykły formularz GET, bez wymogu JavaScriptu), fragmenty, filtry, sortowanie, paginacja (ADR-027)
- [x] Wyszukiwanie po metadanych z wagami i dopasowaniami przybliżonymi `pg_trgm`
- [x] Testy wzorcowe (golden) wyszukiwania na syntetycznym korpusie (w CI)
- [x] Test wydajności (20 000 i 100 000 dokumentów, `make perf`, ręcznie): p95 329 ms przy 20 000 (ADR-028)
- [x] Lokalna ocena trafności na własnych dokumentach (`make relevance`, nigdy w repozytorium)
- [x] Przewodnik wyszukiwania dla użytkowników i administratora (`user-guide.md`); kolumna i filtr autora dla redaktorów (ADR-030); `users list` / `users set-password` (ADR-029)
- [x] Indeksowanie części z tekstem także dla wersji w stanie „wymaga OCR” (`needs_ocr`): ich strony z tekstem są już wyciągnięte
- [x] `search` czyta części przez `ingestion.services.api` i deklaruje `requires=("documents", "ingestion")` (ADR-020)
- [x] `search-check`, `reindex-search`, indeksowanie brakujących wersji przy starcie workera

### Faza 4 — OCR
- [x] Wykrywanie stron bez tekstu (Faza 2: strony oznaczone, wersja „wymaga OCR”)
- [x] OCRmyPDF + Tesseract (`pol+eng`) w procesie potomnym dla oznaczonych stron, także w załącznikach maili; `ocr_applied` dla części i wersji; bez pliku pochodnego, bez Ghostscripta (etap A, ADR-031)
- [x] Po OCR zapisać części ponownie i ponownie zapisać `document.version.extracted` w tej samej transakcji (wyszukiwarka zastępuje wiersze wersji)
- [x] Wgrywanie kilku plików w jednym formularzu, także bez JavaScriptu; duplikaty wstrzymane do decyzji (etap B, ADR-032)
- [x] Osobna kolejka i usługa `ocr-worker`; automatyczny OCR do `OCR_AUTO_MAX_PAGES`, „Uruchom OCR” / „Anuluj OCR”, stany OCR, `baza-wiedzy ocr --pending` (etap B, ADR-033)
- [x] Etykieta „tekst z OCR” w wynikach wyszukiwania i ranking × 0,9 dla tekstu z OCR; pomiary (`make perf-ocr`, fast kontra `tessdata_best`, `--deskew`); złote testy wyszukiwania na wadliwych skanach; `make relevance` czeka na OCR (etap C, ADR-034)

### Faza 5 — Cykl życia dokumentu
- [ ] Wersje, publikacja wybranej wersji (szkic / opublikowany / zarchiwizowany: Faza 3)
- [ ] Kategorie oraz filtr kategorii w wyszukiwaniu
- [ ] Podgląd w przeglądarce (opcjonalnie; ADR-025: osobna domena albo staranna ocena CSP)
- [ ] Edycja tytułu albo metadanych reindeksuje `srch_documents` wersji (zdarzenie albo wywołanie przez `search`)
- [ ] Usuwanie wierszy indeksu wersji, które nie są ani najnowsze, ani opublikowane
- [ ] Obsługa duplikatów

### Faza 6 — Utrzymanie
- [ ] Workflow wydań (tagi → GHCR)
- [ ] `update.sh`, `rollback.sh`
- [ ] `backup.sh`, `restore.sh`, procedura testu odtwarzania
- [ ] `update.sh`, `backup.sh`, `restore.sh` obejmują `ocr-worker` (zatrzymywany na czas migracji i odtwarzania) i `/data/storage/.batches/` (w kopii plików), jak opisuje deployment-operations.md od 0.5.0
- [ ] Kompletny poradnik dla operatora

### Faza 7 — Utwardzenie i odbiór
- [ ] Przegląd bezpieczeństwa względem sekcji 8 pliku `CLAUDE.md`
- [ ] Test odbiorczy na prawdziwych dokumentach (w tym skany i maile)
- [ ] Instrukcja dla czytelników i publikujących
- [ ] Na serwerze: dziennik audytu i limity logowania widzą prawdziwe adresy klientów, nie bramę Dockera (sprawdzenie w [auth-and-permissions.md](auth-and-permissions.md#uwierzytelnianie), adres klienta)
- [ ] Rozdzielenie ról bazy (właściciel schematu do migracji vs rola aplikacji bez praw właściciela) i `REVOKE UPDATE, DELETE, TRUNCATE` na `audit_log`
- [ ] Usunięcie wyjątku `exclude-newer-package` dla `opentelemetry-api` przy comiesięcznym przeglądzie zależności, gdy `exclude-newer` przejdzie 2026-10-30 (ADR-015)
- [ ] Usunięcie wyjątku `exclude-newer-package` dla `soupsieve` przy comiesięcznym przeglądzie zależności w grudniu 2026, gdy `exclude-newer` przekroczy 2026-11-05 (ADR-015, alert Dependabota #2)
- [ ] Na serwerze: worker nie ma dostępu do internetu (sprawdzenie sieci w `make smoke` albo polecenie z deployment-operations.md, Uwagi bezpieczeństwa)
- [ ] Trafność wyszukiwania na prawdziwych (niepoufnych) dokumentach i typowych zapytaniach właściciela (lokalnie, nigdy w repozytorium)
- [ ] Trafność wyszukiwania w tekście z OCR na własnych skanach właściciela (`make relevance … OCR=1`, lokalnie); ponowna decyzja o `tessdata_best` i progu obrotu, jeśli giną słowa (ADR-034)
- [ ] Na serwerze po aktualizacji do 0.5.0: `baza-wiedzy ocr --pending [--max-pages 30]` dla skanów sprzed 0.5.0; sprawdzenie szybkości OCR na procesorze serwera (`docker compose logs ocr-worker`, `seconds=` na dokument)
- [ ] Do oceny (backlog): poprawianie literówek w treści (słownik słów z `ts_stat` + trigramy); wersje z błędem przetwarzania znajdowane po tytule (słownik bez polskich znaków oceniony i odrzucony, ADR-028)
- [ ] Wyszukiwanie przy równoległości: mniej relacji na instrukcję albo PostgreSQL 18, jeśli potrzeba więcej niż ~10 wyszukiwań/s (ADR-028); ustawienia pamięci przed 100 000 dokumentów
- [ ] Usunąć albo skrócić stare logi proxy sprzed 0.4.0, jeśli mogą zawierać tokeny CSRF (dziennik zmian 0.4.0, Security)
