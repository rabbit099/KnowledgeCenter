# Baza Wiedzy — Dokumentacja (polski)

> Dotyczy: v0.1.0 · Ostatnia aktualizacja: 2026-11-12

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
- [x] CI (lint, typy, testy, docs-check, budowanie obrazów, test dymny); workflow wydania przygotowany, jeszcze nieuruchamiany. Zweryfikowane lokalnie tymi samymi celami make; pierwszy przebieg na GitHubie nastąpi przy pierwszym pushu
- [x] `scripts/check_docs.py`

### Faza 1 — Użytkownicy i uprawnienia
- [ ] Lokalny administrator awaryjny (CLI), sesje, CSRF, limit prób logowania
- [ ] Logowanie przez Active Directory, mapowanie grup na role
- [ ] Role i katalog uprawnień, panel admina dla użytkowników i ról
- [ ] Log audytowy i jego przeglądarka

### Faza 2 — Upload i przetwarzanie
- [ ] Upload z walidacją, zapis plików, SHA-256, ostrzeżenie o duplikacie
- [ ] Ekstraktory: PDF (tekst), DOCX, DOC, TXT, EML, MSG (+ załączniki)
- [ ] Pipeline w workerze, statusy, ponowienia, akcja „przetwórz ponownie”
- [ ] Wykrywanie języka dla każdej części

### Faza 3 — Wyszukiwanie
- [ ] Indeksacja w `pl`, `english`, `simple_unaccent`
- [ ] Interfejs wyszukiwania (HTMX), fragmenty, filtry, paginacja
- [ ] Wyszukiwanie po metadanych z `pg_trgm`
- [ ] Testy wzorcowe (golden) wyszukiwania i test wydajności (20 000 dokumentów)

### Faza 4 — OCR
- [ ] Wykrywanie stron bez tekstu, OCRmyPDF (`pol+eng`)
- [ ] Pochodny PDF z warstwą tekstu, flaga `ocr_applied`, limity i timeouty

### Faza 5 — Cykl życia dokumentu
- [ ] Szkic / opublikowany / zarchiwizowany, wersje, publikacja wybranej wersji
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
