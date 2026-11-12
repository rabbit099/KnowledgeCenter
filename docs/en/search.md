# Search

> Applies to: v0.1.0 · Last reviewed: 2026-11-12

Search is PostgreSQL full-text search (FTS), not OCR. OCR only produces text from scanned pages; after that, every document is searched the same way.

## Goals

- The user types a phrase and gets the documents (and e-mail attachments) that contain it.
- Polish inflection works: searching "faktura" also finds "faktury", "fakturze", "fakturą".
- English works the same way ("invoice" finds "invoices").
- Codes, server names, numbers and names that are not in any dictionary are still found.
- Queries typed without Polish diacritics still work ("zolw" finds "żółw").
- Quoted phrases and exclusions work.

## Indexing

Every `srch_parts` row is indexed in three configurations at once:

| Column | Configuration | Handles |
|---|---|---|
| `tsv_pl` | `pl`: Hunspell dictionary, falling back to `simple` for unknown words | Polish inflection |
| `tsv_en` | `english` (built-in stemmer) | English inflection |
| `tsv_simple` | `simple_unaccent`: no stemming, accents removed | Codes, names, numbers, queries without diacritics |

The detected language (`srch_parts.language`) is informational and usable as a filter. It never decides which indexes are used, so mixed-language documents work.

Chunking keeps each tsvector under the 1 MB PostgreSQL limit: PDFs are split per page, other text into chunks of at most 100,000 characters.

The `search` module owns the indexed text and its indexes (table `srch_parts`). It receives text from the `ingestion` module through the event `document.version.extracted` and replaces all parts of that version in one transaction, so repeating the event is harmless. Because the text is stored here, reindexing does not need to read the original files again, and replacing the search engine touches only this module.

## Query processing

Input is the user's string. For each configuration the query is built with `websearch_to_tsquery`, which understands:

- `word1 word2` — both words
- `"exact phrase"` — phrase
- `word1 or word2` — either
- `-word` — exclusion

A part matches when `tsv_pl @@ q_pl OR tsv_en @@ q_en OR tsv_simple @@ q_simple`. An empty query (only stop words) returns an empty result with a hint, not an error.

Metadata (title, file name, e-mail subject and sender) is matched with tsvector and with `pg_trgm` similarity, so partial words and typos still find something there.

## Ranking

- Content: `ts_rank_cd(..., 32)` per configuration, best value wins (the `simple_unaccent` score is multiplied by 0.8 so stemmed matches rank slightly higher).
- Metadata match (title/file name) adds a boost.
- Results are grouped by document; the best part represents the document. The result also shows which part matched (page number, attachment name).

## Visibility (security)

Enforced in SQL for every query:

- user must have `documents.read`;
- normal users: `doc_documents.status = 'published'` and the part belongs to `doc_documents.published_version_id`;
- authors additionally see their own drafts (latest version);
- holders of `documents.read_drafts` see all drafts;
- archived documents are excluded unless the filter explicitly asks and the user may see them.

Old versions are not searchable in the MVP.

The visibility rule is owned by the `documents` module: `documents.services.api` provides the SQL fragment and `search` injects it into its queries (design details are settled in Phase 3).

## Snippets and highlighting

`ts_headline` is expensive and does not escape HTML, so:

1. Run it only for the results on the visible page (default 20).
2. Use sentinel markers as `StartSel`/`StopSel` (strings that cannot appear in normal text).
3. HTML-escape the whole snippet in Python.
4. Replace the sentinels with `<mark>` and `</mark>`.

Never pass a raw snippet to the template as safe HTML.

## Filters and pagination

Filters: category, document kind, language, date range, e-mail sender, "only my documents". Pagination: 20 per page with "load more" through HTMX.

## Illustrative SQL

```sql
WITH q AS (
  SELECT websearch_to_tsquery('pl', :q)              AS q_pl,
         websearch_to_tsquery('english', :q)         AS q_en,
         websearch_to_tsquery('simple_unaccent', :q) AS q_simple
)
SELECT d.id AS document_id, p.id AS part_id,
       GREATEST(ts_rank_cd(p.tsv_pl, q.q_pl, 32),
                ts_rank_cd(p.tsv_en, q.q_en, 32),
                0.8 * ts_rank_cd(p.tsv_simple, q.q_simple, 32)) AS score
FROM srch_parts p
JOIN doc_versions v  ON v.id = p.version_id
JOIN doc_documents d ON d.id = v.document_id
CROSS JOIN q
WHERE (p.tsv_pl @@ q.q_pl OR p.tsv_en @@ q.q_en OR p.tsv_simple @@ q.q_simple)
  AND ( (d.status = 'published' AND v.id = d.published_version_id)
     OR (d.status = 'draft' AND v.id = d.latest_version_id
         AND (d.owner_id = :user_id OR :can_read_drafts)) )
ORDER BY score DESC
LIMIT :limit;
```

This is a sketch: the real implementation groups by document, adds metadata matches and filters.

## Polish dictionary (Hunspell) — setup

1. The `db` image (`docker/db/Dockerfile`) installs the pinned Debian package `hunspell-pl`, verifies the SHA-256 of its files, converts them to UTF-8 and copies `pl_pl.dict`, `pl_pl.affix`, the project's `polish.stop` and the licence file into PostgreSQL's `tsearch_data` directory. The image creates no search objects; its only init script creates the non-superuser application role and database on an empty volume (`docker/db/initdb/10-app-role.sh`).
2. Alembic migrations create the database objects (extensions in the `core` branch, dictionary and configurations in the `search` branch). They run as the application user, which needs no superuser rights (see [deployment-operations.md](deployment-operations.md#configuration)). The objects:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TEXT SEARCH DICTIONARY pl_hunspell (
  TEMPLATE = ispell, DictFile = pl_pl, AffFile = pl_pl, StopWords = polish);

CREATE TEXT SEARCH CONFIGURATION pl (COPY = simple);
ALTER TEXT SEARCH CONFIGURATION pl
  ALTER MAPPING FOR asciiword, word, numword, asciihword, hword, numhword,
                    hword_asciipart, hword_part, hword_numpart
  WITH pl_hunspell, simple;

CREATE TEXT SEARCH CONFIGURATION simple_unaccent (COPY = simple);
ALTER TEXT SEARCH CONFIGURATION simple_unaccent
  ALTER MAPPING FOR asciiword, word, numword, asciihword, hword, numhword,
                    hword_asciipart, hword_part, hword_numpart
  WITH unaccent, simple;
```

3. Verify: `SELECT ts_lexize('pl_hunspell', 'faktury');` returns `{faktura}`. `docker/db/spike.sql` is a diagnostic script that creates temporary copies of these objects in a rolled-back transaction and prints sample results.

### Spike (Phase 0) — must be done first

The Hunspell dictionary must be checked before the rest is built on it:

- Confirm that PostgreSQL loads the chosen Polish dictionary and affix files (affix file formats vary and some do not load).
- Check the dictionary's licence is acceptable for company use.
- Run the golden tests below.
- Record the result here (source and version of the dictionary, test results, known gaps).

Fallbacks if Hunspell fails or quality is poor, in this order:
1. Lemmatization in Python (Morfeusz 2 or a spaCy Polish model) in the worker, storing lemmas in an extra tsvector with the `simple` configuration.
2. Replace the backend with Meilisearch behind the `SearchBackend` interface.

Spike result (2026-11-12): **Hunspell is sufficient; no fallback needed.**

- Dictionary: Debian package `hunspell-pl` `1:25.2.3-1` (Debian 13), built from `libreoffice-dictionaries` 25.2.3; upstream SJP.PL (Marek Futrega). Upstream licence: "GPL or LGPL or MPL or Apache-2.0 or CC-SA-1.0"; the project uses it under **Apache-2.0** (ADR-016). SHA-256 of the original files: `pl_PL.aff` `7c37b9bd…54b38e`, `pl_PL.dic` `215fd73a…110aad` (full values in ADR-016 and `docker/db/dictionary/LICENSE-pl_pl-dictionary.txt`). About 308,000 entries.
- PostgreSQL 16.15 loads the files after conversion to UTF-8 (the package ships ISO-8859-2). Pitfall found: while the package is installed, Debian's `postgresql-common` links `pl_pl.*` into `tsearch_data`; the Dockerfile removes the package and those links before copying plain files.
- `ts_lexize('pl_hunspell', …)`: faktura, faktury, fakturze, fakturą → `{faktura}`; umowa → `{umowa}`; umowie → `{umowie,umowa}`; umów → `{umów,umówić,umowa}`; żółw → `{żółw}`; pracownika, pracownikom → `{pracownik}`; zawarł → `{zawrzeć}`; stop word `nie` → `{}`; unknown word → NULL (falls through to `simple`).
- Golden tests (`make test-db`): 17 passed, 2 skipped (e-mail attachment and OCR cases wait for Phase 2). They run as a non-superuser that owns the database, which proves the migrations will not need superuser rights.
- Cost per database connection (measured with `pg_backend_memory_contexts` and the container's cgroup memory): the dictionary is loaded separately by each backend on first use. **About 27 MB per connection** (context "TS dictionary"); 16 concurrent connections added about 480 MiB to the container. First use: about 0.3 s on one connection, 0.56–0.80 s when 16 connections load it at the same time. Later calls take under 1 ms.
- Known gaps: some words get two lemmas (`umowie` → `umowie`, `umowa`; `zaświadczenie` → also `zaświadczyć`), which can add a few loosely related matches; `zawarta` keeps the participle forms (`zawarta`, `zawarty`) rather than `zawrzeć`. Words missing from the dictionary (names, codes, new terms) are indexed by `simple` unchanged.
- Consequences: keep database connection pools small and fixed (for example app 5 + overflow 5, worker one connection per process) and count about 30 MiB of database memory per connection; do not use a connection-per-request pattern. The `shared_ispell` extension, which shares the dictionary between backends, is not needed at this scale and is outside the stack.

## Reindexing

`tsv_*` columns are generated from `text_content`. After changing a dictionary, stop-word list or configuration, run:

```
docker compose exec app baza-wiedzy reindex-search
```

It rewrites `text_content` in batches (which recomputes the generated columns) and then runs `REINDEX` on the GIN indexes. Document the duration for the current data size here after the first real run.

## Golden tests

`tests/search_golden/` holds queries with the expected documents from synthetic fixtures. Run them with `make test-db` (a throwaway PostgreSQL from `docker/db` on tmpfs, see [deployment-operations.md](deployment-operations.md#developer-commands)); `make test` skips them. Minimum cases:

| Query | Must find a document containing |
|---|---|
| `faktura` | "faktury", "fakturze", "fakturą" |
| `umowa` | "umowie", "umów" |
| `zolw` | "żółw" |
| `invoice` | "invoices" |
| `"zasady bezpieczeństwa"` | the exact phrase, not the two words apart |
| `faktura -korekta` | documents without "korekta" |
| `EX-PVE0` | the literal server name |
| `FV/2026/11/0042` | the literal document number |
| a word only present in an attachment of an e-mail | the e-mail document |
| a word only present on a scanned page | the scanned document (after OCR) |

## Performance target

p95 below 500 ms on a synthetic corpus of 20,000 documents. Measure with the performance test and record results here.
