# Moduł: search

> Dotyczy: v0.0.0 (przed pierwszym wydaniem) · Ostatnia aktualizacja: 2026-11-12

## Zakres

Jest właścicielem zaindeksowanego tekstu i jego indeksów pełnotekstowych, budowania zapytań, rankingu, fragmentów z podświetleniem, filtrów i reindeksacji. Cały kod wyszukiwania stoi za interfejsem `SearchBackend`. Szczegóły: [search.md](../search.md).

## Zależności

Wymaga [documents](documents.md).

## Uprawnienia

Brak własnych. Wyszukiwanie wymaga `documents.read`; widzenie szkiców wymaga `documents.read_drafts`, co jest egzekwowane w SQL przez fragment widoczności dostarczany przez `documents`.

## Tabele

`srch_parts`: jeden wiersz na część tekstu z `text_content`, trzema generowanymi kolumnami `tsv_*`, językiem i numerem strony. Zobacz [data-model.md](../data-model.md).

## Zdarzenia

- Subskrybuje `document.version.extracted`: zastępuje wszystkie części tej wersji w jednej transakcji (idempotentnie).
- Niczego nie publikuje.

## Interfejs publiczny

`modules/search/services/api.py`. Planowane (jeszcze niezaimplementowane): wykonanie wyszukiwania dla użytkownika z filtrami i stroną oraz reindeksacja.

## Punkty rozszerzeń

`SearchBackend`: implementacja na PostgreSQL jest pierwsza; udokumentowane rozwiązanie zapasowe to Meilisearch. Wymiana silnika dotyka wyłącznie tego modułu.

## Status

Faza 0: manifest, pozycja menu, pusty interfejs publiczny i gałąź migracji `search` z `search_0001` (słownik `pl_hunspell`, konfiguracje `pl` i `simple_unaccent`, `depends_on` `core_0001`). Spike słownika polskiego wykonany (Hunspell wystarcza, ADR-016); testy wzorcowe w `tests/search_golden/` działają na prawdziwych migracjach (`make test-db`). Indeksacja, zapytania i interfejs w Fazie 3.
