# Pierwszy prompt dla Claude Code

Wklej poniższy blok jako pierwszą wiadomość w sesji uruchomionej w katalogu repozytorium `baza-wiedzy`. Uzupełnij pola w nawiasach kwadratowych przed wysłaniem. Prompt obejmuje tylko **Fazę 0 (fundament)**; kolejne fazy zlecasz osobnymi promptami (wzór w `INSTRUKCJA_KROK_PO_KROKU.md`, krok 6).

```
Jesteś głównym programistą projektu "Baza Wiedzy" (nazwa robocza) — wewnętrznej firmowej bazy wiedzy z wyszukiwarką pełnotekstową. Projekt jest w całości opisany w repozytorium.

## Co przeczytać na początku
1. CLAUDE.md (zasady projektu — obowiązują bezwzględnie)
2. docs/en/README.md (roadmapa), architecture.md, data-model.md, search.md, deployment-operations.md, decisions.md
(Wersje polskie w docs/pl/ mają tę samą treść.)

## Dane wejściowe
- Repozytorium GitHub: [URL repozytorium, np. git@github.com:konto/baza-wiedzy.git]
- Architektura serwera docelowego: [amd64 / arm64]
- Rejestr obrazów: GHCR, właściciel: [nazwa konta lub organizacji na GitHubie]
- Lokalnie rozwijam na: [macOS / Linux / Windows] z Docker Compose
- Rozmiar: start ok. 200 dokumentów; projektuj i testuj na 20 000 dokumentów i 100 użytkowników

## Zadanie: Faza 0 — Fundament
Zrealizuj WYŁĄCZNIE Fazę 0 z roadmapy (docs/en/README.md):
1. Szkielet repozytorium zgodny z układem z CLAUDE.md sekcja 4: pyproject.toml (uv), Makefile (cele z sekcji 5), pre-commit (ruff, mypy), .gitignore, .env.example, krótki README.md.
2. Docker Compose (produkcyjny i dev): proxy (Caddy), app, worker, db, redis oraz opcjonalny profil `antivirus` z ClamAV. Postgres i Redis bez publikowania portów. Rotacja logów Dockera. Limity pamięci dla workera.
3. Obrazy: docker/app, docker/worker (z tesseract pol+eng, ocrmypdf, libreoffice — na tym etapie wystarczy, że się budują i są wykrywalne w kontenerze), docker/db (Postgres 16 + rozszerzenia pg_trgm i unaccent + słownik Hunspell dla polskiego).
4. SPIKE słownika polskiego (najważniejszy punkt fazy), zgodnie z sekcją „Spike” w docs/en/search.md:
   - pobierz polski słownik Hunspell z wiarygodnego źródła, zapisz źródło, wersję i LICENCJĘ,
   - przygotuj pliki dla PostgreSQL (pl_pl.dict, pl_pl.affix w UTF-8, własna lista polish.stop),
   - utwórz słownik pl_hunspell, konfiguracje `pl` i `simple_unaccent` (init SQL + migracja Alembic),
   - sprawdź `ts_lexize('pl_hunspell', ...)` dla: faktura, faktury, fakturze, fakturą, umowa, umowie, umów, żółw,
   - napisz zalążek testów wzorcowych w tests/search_golden/ na syntetycznych danych (przypadki z search.md) i uruchom je,
   - wynik opisz w docs/en/search.md i docs/pl/search.md (sekcja o spike'u): co działa, czego brakuje, decyzja: Hunspell wystarcza albo przechodzimy na rozwiązanie zapasowe z dokumentacji.
   Jeśli nie możesz pobrać słownika lub PostgreSQL go nie ładuje — zatrzymaj się i powiedz mi, co dokładnie mam zrobić. Nie obchodź problemu po cichu.
5. Aplikacja FastAPI w minimalnej wersji: konfiguracja przez pydantic-settings, /health i /ready (baza, Redis, heartbeat workera, wersja schematu), strona startowa z Jinja2 + HTMX renderująca nazwę z APP_NAME po polsku (gettext/Babel skonfigurowane, katalog pl).
6. Alembic: migracja bazowa (rozszerzenia, konfiguracje wyszukiwania tekstowego). Polecenie CLI `baza-wiedzy migrate`.
7. Worker Dramatiq: minimalny actor testowy i heartbeat widoczny w /ready.
8. CI w GitHub Actions: lint, mypy, testy (z Postgresem i Redisem), docs-check, budowanie obrazów (bez publikacji). Workflow wydania (tag vX.Y.Z → obrazy app, worker, db do GHCR) — przygotuj, ale go nie uruchamiaj.
9. scripts/check_docs.py (reguły z CLAUDE.md sekcja 9) i cel `make docs-check`.

## Czego NIE robić w tej fazie
Logowania, ról, uploadu, ekstraktorów, właściwego OCR, wyszukiwarki w UI, skryptów update/backup. To kolejne fazy.

## Sposób pracy
1. NAJPIERW przedstaw plan (lista plików, wybór wersji narzędzi, ryzyka, kolejność kroków) i zadaj pytania, jeśli coś jest niejasne. Poczekaj na moją akceptację, zanim zaczniesz pisać kod.
2. Potem pracuj w małych commitach (Conventional Commits) na gałęzi `feat/phase-0-foundation`.
3. Każda zmiana zachowania, konfiguracji lub struktury ma od razu aktualizację dokumentacji w docs/en i docs/pl oraz wpis w changelogach w sekcji [Unreleased]. Pliki mają zachować tę samą strukturę nagłówków w obu językach.
4. Zaznacz w docs/*/README.md ukończone punkty Fazy 0.
5. Nie dodawaj zależności ani narzędzi spoza stacku z CLAUDE.md bez wpisu ADR i uzasadnienia — pytaj mnie.
6. Pamiętaj o zasadach bezpieczeństwa z CLAUDE.md sekcja 8 (kontenery bez roota, brak sekretów w repozytorium, wyłącznie syntetyczne fixture'y).

## Kryteria ukończenia Fazy 0
- `make up` uruchamia wszystkie usługi, a `curl -k https://localhost/ready` zwraca OK (w dev możesz użyć HTTP, jeśli opiszesz to w dokumentacji).
- `make test`, `make lint`, `make docs-check` przechodzą lokalnie i w CI.
- Spike słownika polskiego jest wykonany i opisany, wraz z licencją słownika.
- Obrazy app, worker i db budują się w CI.
- Dokumentacja EN i PL jest spójna z tym, co zbudowano.

## Raport końcowy
Na końcu napisz: co zrobiono, jak to ręcznie sprawdzić (konkretne polecenia), wynik spike'a słownika i rekomendację, jakie zmiany w dokumentacji i planie wynikają z tych wyników, otwarte pytania do mnie.
```

## Po zakończeniu Fazy 0

Sprawdź według listy z kroku 6 instrukcji. Najważniejsze: wynik spike'a słownika polskiego. Jeśli Hunspell się nie sprawdził, zdecydujcie o rozwiązaniu zapasowym (lematyzacja w Pythonie albo Meilisearch) **przed** Fazą 2 i 3, bo od tego zależy indeksacja.
