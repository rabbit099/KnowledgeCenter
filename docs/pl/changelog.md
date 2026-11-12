# Dziennik zmian

> Dotyczy: v0.0.0 (przed pierwszym wydaniem) · Ostatnia aktualizacja: 2026-11-12

Format oparty na [Keep a Changelog](https://keepachangelog.com/pl/). Wersjonowanie: [SemVer](https://semver.org/lang/pl/).

## [Unreleased]

### Added
- Aplikacja WWW (FastAPI): strona startowa po polsku z nazwą z `APP_NAME` (Jinja2, HTMX serwowany lokalnie, katalog gettext `pl`), `/health`, `/ready` (sprawdzanie bazy i Redisa; JSON, 503 przy błędzie) oraz fragment stanu ładowany przez HTMX. Nowe polecenie `make messages`. Rozwiązywanie zależności wybiera tylko wydania starsze niż ustalona data (`exclude-newer`).
- Redis wymaga hasła (`REDIS_PASSWORD`, nowa zmienna). Wolumen plików nazywa się `storage` i jest montowany w `/data/storage`. W dev `app` i `worker` zatrzymują się po 3 nieudanych startach zamiast restartować się w nieskończoność.
- Stos Docker Compose (`docker-compose.yml`, `docker-compose.dev.yml`): proxy (Caddy, bez roota, tryby TLS `internal`/`files`/`acme`), app, worker, db, redis, opcjonalny profil `antivirus` (ClamAV); rotacja logów, limity pamięci, jawne `max_connections`; obrazy app i worker (bez roota; worker z Tesseractem `pol`+`eng`, OCRmyPDF, LibreOffice Writer); obraz `db` przy pierwszym starcie tworzy zwykłego użytkownika aplikacji. Nowe zmienne w `.env.example`.
- Spike polskiego słownika wykonany: Hunspell wystarcza (wyniki, zmierzona pamięć i braki w `search.md`, ADR-016). Testy wzorcowe wyszukiwania w `tests/search_golden/` i `make test-db`, które uruchamia je na jednorazowej bazie w Dockerze bez publikowania portu.
- Obraz bazy danych `docker/db` (PostgreSQL 16.15 na Debianie 13) z polskim słownikiem Hunspell (`pl_pl.dict`, `pl_pl.affix`, UTF-8), własną listą `polish.stop` i plikiem licencji słownika; `docker/db/spike.sql` to skrypt diagnostyczny słownika.
- Szkielet repozytorium: projekt Python 3.12 zarządzany przez `uv`, `Makefile` jako jedyny punkt wejścia dla poleceń, pre-commit (ruff, mypy), `.env.example`, końce linii LF.
- `scripts/check_docs.py` i `make docs-check`: sprawdzają, że dokumentacja angielska i polska (także `modules/`) ma te same pliki, nagłówki i linie nagłówkowe , że oba dzienniki zmian mają `[Unreleased]` oraz że każdy moduł w `src/baza_wiedzy/modules/` ma stronę w `docs/en/modules/` i `docs/pl/modules/`.
- Struktura modularna: zawsze włączony rdzeń oraz moduły `documents`, `ingestion`, `search` (na razie puste manifesty) wybierane przez `ENABLED_MODULES`; sprawdzanie zależności przy starcie z czytelnymi błędami; katalog uprawnień składany z manifestów modułów; magistrala zdarzeń w obrębie procesu; granice modułów sprawdzane przez import-linter w `make lint`.
- Dokumentacja projektu modularnego: ADR-013 do ADR-015, strony modułów w `docs/*/modules/`, nowe sekcje w architekturze, modelu danych, wdrożeniu i utrzymaniu.
- Planowane nazwy tabel mają teraz prefiks modułu (`doc_*`, `srch_*`, `ing_*`); tabela z zaindeksowanym tekstem to `srch_parts` (wcześniej `document_parts`). Tylko dokumentacja; tabele jeszcze nie istnieją.
- Przewodnik projektu `CLAUDE.md` oraz dwujęzyczny zestaw dokumentacji (architektura, model danych, wyszukiwanie, przetwarzanie i OCR, uwierzytelnianie i uprawnienia, wdrożenie i utrzymanie, decyzje, plan prac).
