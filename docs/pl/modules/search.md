# Moduł: search

> Dotyczy: v0.5.0 · Ostatnia aktualizacja: 2026-11-27

## Zakres

Jest właścicielem zaindeksowanego tekstu i jego indeksów pełnotekstowych, budowania zapytań, rankingu, fragmentów z podświetleniem, filtrów i reindeksacji. Cały kod wyszukiwania stoi za interfejsem `SearchBackend`. Szczegóły: [search.md](../search.md).

## Zależności

Wymaga [documents](documents.md) (widoczność, zakres wyszukiwania, metadane) i [ingestion](ingestion.md) (wyodrębniony tekst, ADR-020); `ENABLED_MODULES` bez `ingestion` kończy start czytelnym błędem.

## Uprawnienia

Brak własnych. Wyszukiwanie wymaga `documents.read`; widzenie szkiców wymaga `documents.read_drafts`, co jest egzekwowane w SQL przez fragment widoczności dostarczany przez `documents`.

## Tabele

`srch_parts` (jeden wiersz na część tekstu: `text_content`, `ident_terms`, trzy generowane kolumny `tsv_*`, pochodzenie, strona, język) i `srch_documents` (jeden wiersz na wersję: metadane z wagami, `meta_fold` z indeksem trigramowym), obie z `search_0002`; `srch_parts.ocr_applied` z `search_0003` (ranking × 0,9 i etykieta „tekst z OCR”, ADR-034). Zobacz [data-model.md](../data-model.md).

## Zdarzenia

- Subskrybuje `document.version.extracted`: czyta części przez `ingestion.services.api.parts_of` i metadane przez `documents.services.api.index_metadata` i zastępuje wszystkie wiersze tej wersji w jednej transakcji pod blokadą doradczą (idempotentnie; zdarzenie jest dostarczane co najmniej raz przez outbox ingestion i niesie tylko identyfikatory, ADR-020). Błąd zostawia zdarzenie niedostarczone, więc zostanie dostarczone ponownie.
- Start workera (`worker_startup`): indeksuje każdą wyodrębnioną wersję bez wierszy, tylko w jednym procesie workera (sesyjna blokada doradcza; pozostałe pomijają).
- Niczego nie publikuje.

## Interfejs publiczny

`modules/search/services/api.py` na razie nic nie udostępnia innym modułom (nikt tego nie potrzebuje). Dla operatora: `baza-wiedzy search-check` i `baza-wiedzy reindex-search --all | --missing | --document ID | --version ID` ([search.md](../search.md#reindeksacja)). Korzysta z `documents.services.api` (`search_scope`, `ScopeFilters`, `KIND_GROUPS`, `index_metadata`, `visible_owners`) i `ingestion.services.api` (`parts_of`, `text_part_counts`).

## Punkty rozszerzeń

`SearchBackend`: implementacja na PostgreSQL jest pierwsza; udokumentowane rozwiązanie zapasowe to Meilisearch. Wymiana silnika dotyka wyłącznie tego modułu.

## Status

Faza 0: manifest, pozycja menu, pusty interfejs publiczny i gałąź migracji `search` z `search_0001` (słownik `pl_hunspell`, konfiguracje `pl` i `simple_unaccent`, `depends_on` `core_0001`). Spike słownika polskiego wykonany (Hunspell wystarcza, ADR-016); testy wzorcowe w `tests/search_golden/` działają na prawdziwych migracjach (`make test-db`). Faza 3, etap B (ADR-026, ADR-027): tabele indeksu (`search_0002`, `depends_on` `ingestion_0001` i `documents_0004`), indeksowanie po zdarzeniu i przy starcie workera, normalizacja identyfikatorów (`identifiers.py`), backend PostgreSQL (`postgres.py`: trzy konfiguracje, ranking, metadane z wagami i trigramami, wykluczenia na poziomie dokumentu, limit czasu z wynikami zapasowymi według daty, fragmenty z wartownikami), strona `/search` (uprawnienie `documents.read`) i pole wyszukiwania na stronie głównej, filtry, sortowanie, strony, CLI `search-check` i `reindex-search`, testy golden na syntetycznym korpusie. Ustawienia `SEARCH_*`. Etap C: wydajność (20 000 i 100 000 dokumentów).
