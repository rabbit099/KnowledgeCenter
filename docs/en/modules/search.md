# Module: search

> Applies to: v0.0.0 (pre-release) · Last reviewed: 2026-11-12

## Scope

Owns the indexed text and its full-text indexes, query building, ranking, snippets with highlighting, filters and reindexing. All search code sits behind the `SearchBackend` interface. Details: [search.md](../search.md).

## Dependencies

Requires [documents](documents.md).

## Permissions

None of its own. Searching needs `documents.read`; seeing drafts needs `documents.read_drafts`, enforced in SQL through the visibility fragment provided by `documents`.

## Tables

`srch_parts`: one row per text part with `text_content`, the three generated `tsv_*` columns, language and page number. See [data-model.md](../data-model.md).

## Events

- Subscribes to `document.version.extracted`: replaces all parts of that version in one transaction (idempotent).
- Publishes nothing.

## Public interface

`modules/search/services/api.py`. Planned (not implemented yet): run a search for a user with filters and a page, and reindex.

## Extension points

`SearchBackend`: the PostgreSQL implementation is the first one; the documented fallback is Meilisearch. Replacing the engine touches only this module.

## Status

Phase 0: manifest, a menu entry, an empty public interface and the migration branch `search` with `search_0001` (dictionary `pl_hunspell`, configurations `pl` and `simple_unaccent`, `depends_on` `core_0001`). The Polish dictionary spike is done (Hunspell is sufficient, ADR-016); golden tests in `tests/search_golden/` run on the real migrations (`make test-db`). Indexing, queries and the UI come in Phase 3.
