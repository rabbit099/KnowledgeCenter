# Baza Wiedzy — Dokumentacja (polski)

> Dotyczy: v0.3.0 · Ostatnia aktualizacja: 2026-11-18

Baza Wiedzy (nazwa robocza) to wewnętrzna aplikacja webowa. Użytkownicy wgrywają dokumenty (PDF, DOCX, DOC, TXT, EML, MSG), system odczytuje ich tekst (OCR dla skanów) i wyszukuje dokumenty zawierające wpisaną frazę. Działa w Dockerze na serwerze z Linuksem.

Angielska wersja tej dokumentacji: [`../en/`](../en/README.md).

## Spis treści

| Dokument | Czego dotyczy |
|---|---|
| [architecture.md](architecture.md) | Usługi, przepływ danych, stack technologiczny |
| [data-model.md](data-model.md) | Tabele bazy danych, relacje, indeksy |
| [search.md](search.md) | Jak działa wyszukiwanie: indeksacja polska i angielska, ranking, fragmenty |
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
- [ ] Indeksacja w `pl`, `english`, `simple_unaccent`
- [ ] Interfejs wyszukiwania (HTMX), fragmenty, filtry, paginacja
- [ ] Wyszukiwanie po metadanych z `pg_trgm`
- [ ] Testy wzorcowe (golden) wyszukiwania i test wydajności (20 000 dokumentów)
- [ ] Indeksowanie części z tekstem także dla wersji w stanie „wymaga OCR” (`needs_ocr`): ich strony z tekstem są już wyciągnięte
- [ ] `search` czyta części przez `ingestion.services.api` i deklaruje `requires=("documents", "ingestion")` (ADR-020)

### Faza 4 — OCR
- [x] Wykrywanie stron bez tekstu (Faza 2: strony oznaczane, wersja „wymaga OCR”)
- [ ] OCRmyPDF (`pol+eng`) dla oznaczonych stron wersji `needs_ocr`, potem `done`
- [ ] Pochodny PDF z warstwą tekstu, flaga `ocr_applied`, limity i timeouty

### Faza 5 — Cykl życia dokumentu
- [ ] Wersje, publikacja wybranej wersji (szkic / opublikowany / zarchiwizowany: Faza 3)
- [ ] Kategorie
- [ ] Podgląd w przeglądarce (opcjonalnie)

### Faza 6 — Utrzymanie
- [ ] Workflow wydań (tagi → GHCR)
- [ ] `update.sh`, `rollback.sh`
- [ ] `backup.sh`, `restore.sh`, procedura testu odtwarzania
- [ ] Kompletny poradnik dla operatora

### Faza 7 — Utwardzenie i odbiór
- [ ] Przegląd bezpieczeństwa względem sekcji 8 pliku `CLAUDE.md`
- [ ] Test odbiorczy na prawdziwych dokumentach (w tym skany i maile)
- [ ] Instrukcja dla czytelników i publikujących
- [ ] Na serwerze: dziennik audytu i limity logowania widzą prawdziwe adresy klientów, nie bramę Dockera (sprawdzenie w [auth-and-permissions.md](auth-and-permissions.md#uwierzytelnianie), adres klienta)
- [ ] Rozdzielenie ról bazy (właściciel schematu do migracji vs rola aplikacji bez praw właściciela) i `REVOKE UPDATE, DELETE, TRUNCATE` na `audit_log`
- [ ] Usunięcie wyjątku `exclude-newer-package` dla `opentelemetry-api` przy comiesięcznym przeglądzie zależności, gdy `exclude-newer` przejdzie 2026-10-30 (ADR-015)
- [ ] Na serwerze: worker nie ma dostępu do internetu (sprawdzenie sieci w `make smoke` albo polecenie z deployment-operations.md, Uwagi bezpieczeństwa)
