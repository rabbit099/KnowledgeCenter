# Module: search

> Applies to: v0.4.1 · Last reviewed: 2026-11-20

## Scope

Owns the indexed text and its full-text indexes, query building, ranking, snippets with highlighting, filters and reindexing. All search code sits behind the `SearchBackend` interface. Details: [search.md](../search.md).

## Dependencies

Requires [documents](documents.md) (visibility, search scope, metadata) and [ingestion](ingestion.md) (the extracted text, ADR-020); `ENABLED_MODULES` without `ingestion` fails at start with a readable error.

## Permissions

None of its own. Searching needs `documents.read`; seeing drafts needs `documents.read_drafts`, enforced in SQL through the visibility fragment provided by `documents`.

## Tables

`srch_parts` (one row per text part: `text_content`, `ident_terms`, the three generated `tsv_*` columns, origin, page, language) and `srch_documents` (one row per version: metadata with weights, `meta_fold` with a trigram index), both from `search_0002`. See [data-model.md](../data-model.md).

## Events

- Subscribes to `document.version.extracted`: reads the parts through `ingestion.services.api.parts_of` and the metadata through `documents.services.api.index_metadata` and replaces all rows of that version in one transaction under an advisory lock (idempotent; the event is delivered at least once by the ingestion outbox and carries identifiers only, ADR-020). A failure leaves the event undelivered, so it is delivered again.
- Worker start (`worker_startup`): indexes every extracted version that has no rows, in one worker process only (session-level advisory lock; the others skip).
- Publishes nothing.

## Public interface

`modules/search/services/api.py` offers nothing to other modules yet (nobody needs it). Operators: `baza-wiedzy search-check` and `baza-wiedzy reindex-search --all | --missing | --document ID | --version ID` ([search.md](../search.md#reindexing)). It uses `documents.services.api` (`search_scope`, `ScopeFilters`, `KIND_GROUPS`, `index_metadata`, `visible_owners`) and `ingestion.services.api` (`parts_of`, `text_part_counts`).

## Extension points

`SearchBackend`: the PostgreSQL implementation is the first one; the documented fallback is Meilisearch. Replacing the engine touches only this module.

## Status

Phase 0: manifest, a menu entry, an empty public interface and the migration branch `search` with `search_0001` (dictionary `pl_hunspell`, configurations `pl` and `simple_unaccent`, `depends_on` `core_0001`). The Polish dictionary spike is done (Hunspell is sufficient, ADR-016); golden tests in `tests/search_golden/` run on the real migrations (`make test-db`). Phase 3, stage B (ADR-026, ADR-027): index tables (`search_0002`, `depends_on` `ingestion_0001` and `documents_0004`), indexing by the event and at worker start, identifier normalisation (`identifiers.py`), the PostgreSQL backend (`postgres.py`: three configurations, ranking, metadata with weights and trigrams, exclusions per document, time limit with a fallback by date, snippets with sentinels), the page `/search` (permission `documents.read`) and the search field on the start page, filters, sorting, pages, CLI `search-check` and `reindex-search`, golden tests on the synthetic corpus. Settings `SEARCH_*`. Stage C: performance (20,000 and 100,000 documents).
