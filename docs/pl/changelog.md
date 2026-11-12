# Dziennik zmian

> Dotyczy: v0.0.0 (przed pierwszym wydaniem) · Ostatnia aktualizacja: 2026-11-12

Format oparty na [Keep a Changelog](https://keepachangelog.com/pl/). Wersjonowanie: [SemVer](https://semver.org/lang/pl/).

## [Unreleased]

### Added
- Szkielet repozytorium: projekt Python 3.12 zarządzany przez `uv`, `Makefile` jako jedyny punkt wejścia dla poleceń, pre-commit (ruff, mypy), `.env.example`, końce linii LF.
- `scripts/check_docs.py` i `make docs-check`: sprawdzają, że dokumentacja angielska i polska (także `modules/`) ma te same pliki, nagłówki i linie nagłówkowe oraz że oba dzienniki zmian mają `[Unreleased]`.
- Struktura modularna: zawsze włączony rdzeń oraz moduły `documents`, `ingestion`, `search` (na razie puste manifesty) wybierane przez `ENABLED_MODULES`; sprawdzanie zależności przy starcie z czytelnymi błędami; katalog uprawnień składany z manifestów modułów; magistrala zdarzeń w obrębie procesu; granice modułów sprawdzane przez import-linter w `make lint`.
- Dokumentacja projektu modularnego: ADR-013 do ADR-015, strony modułów w `docs/*/modules/`, nowe sekcje w architekturze, modelu danych, wdrożeniu i utrzymaniu.
- Planowane nazwy tabel mają teraz prefiks modułu (`doc_*`, `srch_*`, `ing_*`); tabela z zaindeksowanym tekstem to `srch_parts` (wcześniej `document_parts`). Tylko dokumentacja; tabele jeszcze nie istnieją.
- Przewodnik projektu `CLAUDE.md` oraz dwujęzyczny zestaw dokumentacji (architektura, model danych, wyszukiwanie, przetwarzanie i OCR, uwierzytelnianie i uprawnienia, wdrożenie i utrzymanie, decyzje, plan prac).
