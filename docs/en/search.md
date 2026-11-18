# Search

> Applies to: v0.3.0 · Last reviewed: 2026-11-18

Search is PostgreSQL full-text search (FTS), not OCR. OCR only produces text from scanned pages; after that, every document is searched the same way.

## Goals

- The user types a phrase and gets the documents (and e-mail attachments) that contain it.
- Polish inflection works: searching "faktura" also finds "faktury", "fakturze", "fakturą".
- English works the same way ("invoice" finds "invoices").
- Codes, server names, numbers and names that are not in any dictionary are still found, also when written with other separators ("725-18-01-126" = "7251801126").
- Queries typed without Polish diacritics still work ("zolw" finds "żółw").
- Quoted phrases and exclusions work.

## How to search (for users)

The search field is on the start page and under "Szukaj" (`/search`); it needs the permission `documents.read` (every default role has it).

| Type | Finds |
|---|---|
| `umowa serwisowa` | documents containing both words, in any form ("umowy serwisowej", "umowie") |
| `"zasady bezpieczeństwa"` | the exact phrase (words next to each other) |
| `faktura -korekta` | documents with "faktura" and without "korekta" anywhere in the document |
| `faktura or rachunek` | documents with either word (`or` in English, lower case) |
| `725-18-01-126`, `7251801126` | the same tax number written with or without dashes |
| `603 388 100`, `+48 603 388 100` | the same phone number with spaces, without spaces, with or without +48 |
| `17/2026` | also "UM/17/2026" (a part between separators) |
| `example.com`, `192.168.10` | e-mail addresses and host names in that domain, addresses in that network |

Results are grouped by document (the best fragment is shown with the hits marked, and where it is: "strona 7", "załącznik: umowa.pdf › strona 2"); drafts are marked "szkic" and are found only by their author and by holders of `documents.read_drafts`; archived documents are hidden unless "Pokaż zarchiwizowane" is ticked (authors and `documents.edit_any` only). Filters: file type, language of the text, author (the person who uploaded), date range (e-mail: sending date, otherwise upload date); sorting by relevance or date; 20 results per page, at most 50 pages. A query needs 2 to 200 characters. Small typos in a title, file name, e-mail subject or sender are tolerated (not in numbers and not in the text).

Known limits (by design, not errors):
- Without Polish letters a word is found only in exactly that form: `lodz` finds "Łódź" but not "w Łodzi"; `lodzi` finds "w Łodzi". Typed with Polish letters, every form is found (`Łódź` finds "w Łodzi"). Reason: the Polish dictionary works on words with diacritics; the no-diacritics index does not know inflection.
- Names and words missing from the dictionary are found only in the form of the text ("Kowalski" does not find "Kowalskim").
- Numbers: a piece inside one segment ("0042" in "00042"), the beginning of a number ("7251*") and a piece not ending at a separator ("3388" in "603 388 100") are not found.
- Scanned pages are found only after OCR (Phase 4); documents whose processing failed are not found (open them from "Moje dokumenty").
- Search phrases are not stored, audited or logged anywhere on the server (ADR-025); your browser keeps them in its history.

## Indexing

Every `srch_parts` row is indexed in three configurations at once (ADR-003, kept by ADR-026):

| Column | Configuration | Handles |
|---|---|---|
| `tsv_pl` | `pl`: Hunspell dictionary, falling back to `simple` for unknown words | Polish inflection |
| `tsv_en` | `english` (built-in stemmer) | English inflection |
| `tsv_simple` | `simple_unaccent`: no stemming, accents removed | Codes, names, numbers, queries without diacritics |

Each column is generated from `text_content` plus `ident_terms`, the normalised identifiers of the text (next sections). The detected language (`srch_parts.language`) is used for ranking and as a filter; it never decides which indexes are used, so mixed-language documents work.

Chunking keeps each tsvector under the 1 MB PostgreSQL limit: PDFs are split per page, other text into chunks of at most 100,000 characters (done by ingestion).

`srch_documents` holds one row per version with the metadata matched separately: title and e-mail subject (weight A), file name with separators turned into spaces, sender and recipients (weight B), in the same three configurations, and `meta_fold` (lower case, no diacritics) with a trigram index for near matches.

The `search` module owns its index; the text is owned by `ingestion` (`ing_parts`, ADR-020), the metadata by `documents`. On the event `document.version.extracted` (identifiers only, delivered at least once through the ingestion outbox) `search` reads the parts with `ingestion.services.api.parts_of` and the metadata with `documents.services.api.index_metadata` and replaces all rows of that version in one transaction under an advisory lock, so repeating the event is harmless. Parts without text (a scanned page before OCR) are skipped. The worker start indexes every extracted version that has no rows (a new installation, a lost event). Rows are per version; which version is searched (the published one, or the latest of a draft) is decided by `documents.services.api.search_scope`, so publishing, unpublishing, archiving and restoring take effect at once without reindexing, and Phase 5 can add versions without rebuilding the index. After OCR (Phase 4) ingestion stores the parts again and writes the event again, and search replaces the rows.

## Query processing

Input is the user's string (2–200 characters after collapsing spaces; always a bound parameter). For each configuration the query is built with `websearch_to_tsquery`, which understands:

- `word1 word2` — both words
- `"exact phrase"` — phrase
- `word1 or word2` — either
- `-word` — exclusion

Each of the three queries is OR-ed with two rewritten forms in which every identifier is joined (the second also without a leading country code), see the next section. A part matches when `tsv_pl @@ q_pl OR tsv_en @@ q_en OR tsv_simple @@ q_simple`. An exclusion removes the whole document when any of its parts or its metadata contains the excluded word in any configuration (not only the matching part). A query of very common words only (all removed as stop words) returns no result with a hint.

Metadata (title, file name, e-mail subject, sender, recipients) is matched with the tsvectors of `srch_documents` and, for queries of at least 4 characters without digits and without operators, with `pg_trgm` word similarity (`SEARCH_TRIGRAM_THRESHOLD`, default 0.6), so typos in a title still find it. Numbers never match by similarity ("FV/2026/11/0042" must not find "…0043").

## Identifiers (numbers and codes)

The text parser splits or glues identifiers differently in a document and a query: "UM/17/2026" is one token (so "17/2026" would not find it), "603 388 100" three tokens, "IT-002" becomes "it" and "-002", an e-mail address one token (so its domain would not find it). Therefore `search/identifiers.py` adds normalised tokens without separators at indexing (`ident_terms`, in all three tsvectors) and rewrites the query the same way (ADR-026):

| Recognised | Example | Extra tokens |
|---|---|---|
| Letters/digits joined by `-` or `/`, with a digit | `UM/17/2026`, `725-18-01-126`, `IT-002`, `EX-PVE0` | every run of segments joined and every segment: `um172026`, `172026`, `um17`, `2026`, … |
| Digit groups separated by spaces or dashes, ≥ 6 digits, optional `+` | `603 388 100`, `+48 603 388 100` | every run of groups joined: `603388100`, `48603388100`, `388100`, … |
| E-mail addresses | `ksiegowosc@example.com` | local part and its pieces, domain and parent domains |
| Host names | `intranet.example.com` | the name and its parent domains |
| IPv4 addresses | `192.168.10.25` | the address and its first three octets |

The query gets the joined form of each identifier as an alternative (`725-18-01-126` → `7251801126`, `+48 603 388 100` → `48603388100` and `603388100`, `+48501222333` → `48501222333`); words keep their diacritics so the Polish dictionary still applies ("umowie 17/2026" finds "Umowa UM/17/2026"). The original query is always kept, so nothing it finds is lost.

Does not work (by design): a part of one segment ("0042" in "00042"); prefixes ("7251*"); a run not ending at a separator ("3388" in "603 388 100"); letters and digits separated only by spaces ("UM 17 2026"); other country prefixes ("0048"); IPv6; ranges and CIDR; typos in numbers. Cost: a few extra tokens per identifier (a chain of n segments gives up to n(n+1)/2, at most 8 segments joined in all combinations, at most 5,000 tokens per part); joined numbers next to each other in prose ("2019 2020 2021") create tokens nobody searches — harmless.

## Ranking

Per document (ADR-027):
- content: the best part; `ts_rank_cd(…, 32)` per configuration, best value wins; `simple_unaccent` × 0.8 (stemmed matches rank slightly higher); the configuration of the other language than the part's detected one × 0.9 ("unknown": both full);
- plus metadata: `ts_rank(…, 32)` with the weights A (title, subject) = 1.0 and B (file name, sender, recipients) = 0.4, so a match in the title ranks above one in the text; a trigram near match counts `0.3 × similarity`, below an exact match;
- ties: newer document first, then by id (deterministic).

Sorting by date: newest first (e-mail: sending date, otherwise upload date), relevance second. The result shows which part matched (page number, attachment path).

## Visibility (security)

Enforced in SQL for every query:

- the documents a user may see are exactly those of `visibility_clause` ([auth-and-permissions.md](auth-and-permissions.md#document-visibility)): published ones with `documents.read`, all drafts with `documents.read_drafts`, own documents in every state; drafts are marked "szkic" in the results;
- the searched version is the published one (`published_version_id`) for published and archived documents and the latest one for drafts (`Document.current_version_id`);
- archived documents are excluded unless the filter "pokaż zarchiwizowane" asks for them and the user may see them (owner, `documents.edit_any`); Readers never see them.

Search phrases are never logged, audited or stored (ADR-025).

Old versions are not searchable in the MVP.

The visibility rule is owned by the `documents` module: `documents.services.api` provides the SQL fragment and `search` injects it into its queries (design details are settled in Phase 3).

## Snippets and highlighting

`ts_headline` is expensive and does not escape HTML, so:

1. It runs only for the best part of each result on the visible page (at most 20), in the configuration that matched (`MaxWords=35, MinWords=15, MaxFragments=2`).
2. The highlight uses two private-use characters (U+E000, U+E001) as `StartSel`/`StopSel`; the indexer removes them from every text, so a document cannot fake a highlight.
3. The whole snippet is HTML-escaped in Python (`snippets.to_html`).
4. Only then the sentinels become `<mark>` and `</mark>`.

`ts_headline` drops markup tokens such as `<script>` by itself; whatever text remains is escaped. Never pass a raw snippet to a template as safe HTML. A document found only through its metadata shows "Dopasowanie w tytule, nazwie pliku albo nagłówkach maila" instead of a snippet.

## Filters and pagination

Filters (all in the URL of a plain GET form, applied in the SQL of `search_scope`): file type (PDF, Word = DOC/DOCX, e-mail = EML/MSG, text = TXT), language of the matching part (`pl`, `en`, `unknown`; metadata-only matches are left out when a language is chosen), author (owner of the document), date from/to (local days, Europe/Warsaw; e-mail: sending date, otherwise upload date), "Pokaż zarchiwizowane" (only for users who may see archived documents). Categories come in Phase 5. Sorting: relevance or date. Pages: `SEARCH_PAGE_SIZE` (20) results, numbered links, at most `SEARCH_MAX_PAGES` (50); the number of documents found is exact (`count(*) OVER ()`, free while ranking reads every match).

## Limits and privacy

- Each search runs in one transaction with `SET LOCAL statement_timeout = SEARCH_TIMEOUT_MS` (3,000 ms). If the ranked search exceeds it (a very general query), it runs again without ranking, newest first, and the page says so; if that also exceeds it, the user is asked to narrow the query.
- The query is always a bound parameter; length 2–200 characters (`SEARCH_MIN_QUERY_CHARS`, `SEARCH_MAX_QUERY_CHARS`).
- Search phrases never reach a log, the audit or an error message (ADR-025). Each search writes one line with counters only: `search: results=12 page=1 sort=relevance filters=kind,date fallback=no duration_ms=48` (names of the filters used, never their values); a double timeout writes `search: time limit exceeded twice`.

## Illustrative SQL

Simplified shape of the statement built by `search/postgres.py` (the real one adds the identifier alternatives, the metadata part, exclusions and filters):

```sql
WITH q AS (
  SELECT websearch_to_tsquery('pl', :q)              || websearch_to_tsquery('pl', :alt)              AS pl,
         websearch_to_tsquery('english', :q)         || websearch_to_tsquery('english', :alt)         AS en,
         websearch_to_tsquery('simple_unaccent', :q) || websearch_to_tsquery('simple_unaccent', :alt) AS simple
), content AS (
  SELECT p.document_id, p.id AS part_id,
         GREATEST(ts_rank_cd(p.tsv_pl, q.pl, 32), ts_rank_cd(p.tsv_en, q.en, 32),
                  0.8 * ts_rank_cd(p.tsv_simple, q.simple, 32)) AS score
  FROM srch_parts p
  JOIN (/* documents.services.api.search_scope(user, filters) */) s ON s.version_id = p.version_id
  CROSS JOIN q
  WHERE p.tsv_pl @@ q.pl OR p.tsv_en @@ q.en OR p.tsv_simple @@ q.simple
), best AS (
  SELECT DISTINCT ON (document_id) * FROM content ORDER BY document_id, score DESC, part_id
)
SELECT document_id, part_id, score, count(*) OVER () AS total
FROM best ORDER BY score DESC, document_id LIMIT 20 OFFSET 0;
```

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
- Consequences: keep database connection pools small and fixed (for example app 5 + overflow 5, worker two connections per process) and count about 30 MiB of database memory per connection; do not use a connection-per-request pattern. The `shared_ispell` extension, which shares the dictionary between backends, is not needed at this scale and is outside the stack.

## Reindexing

The `tsv_*` columns are generated from `text_content` and `ident_terms`. Commands (`docker compose exec app baza-wiedzy …`):

| Command | When | Expected output |
|---|---|---|
| `search-check` | any time; after an upgrade; when a document is not found | `Extracted versions: N`, counts of `not indexed`, `different number of parts`, `indexed but no longer extracted` (identifiers only), then `Search index OK.` (exit 0) or a hint to reindex (exit 1) |
| `reindex-search --missing` | `search-check` reported differences | `Indexed N version(s); removed M orphaned; took T s.` |
| `reindex-search --all` | after changing a dictionary, stop-word list, configuration or the identifier rules | the same line; rewrites every row (which recomputes the generated columns), then `REINDEX` of both tables |
| `reindex-search --document ID` / `--version ID` | one document | the same line |

The worker start runs the `--missing` part by itself (log line `search: indexed N version(s) missing from the index`). Document the duration of `--all` for the current data size here after the first real run.

## Golden tests

`tests/search_golden/` runs with `make test-db` (a throwaway PostgreSQL from `docker/db` on tmpfs, in CI too); `make test` skips them.

- `test_golden.py`: the dictionary and the three configurations on short texts (base forms, stop words, phrases, exclusion, codes).
- `test_corpus.py`: the synthetic Polish-English corpus `tests/fixtures/search_corpus.py` (26 documents, 48 queries; the same files `make probki` writes to `wyszukiwanie/` with `OCZEKIWANE-wyszukiwanie.txt`). Every file goes through upload, extraction, indexing by the event and publication; every query runs through `/search` as a Reader, as the author or as Admin and is compared with "must find", "must not find" and "first result". Covered: inflection (umowa/umowy/umowie), diacritics (Łódź/lodz), names, phrases, exclusion in another part, bilingual queries, English plurals, words only in an e-mail attachment, subject or sender, a partly scanned PDF, a word only in the file name, a typo in a title, drafts and archived documents per role, identifiers (tax numbers, contract numbers, phone numbers, host names, codes, e-mail addresses, domains, IP addresses). `lodz` not finding "w Łodzi" is a documented case (a "must not find" expectation), not a failure.
- Also there: escaping of HTML in documents, snippets and their location, sorting, every filter, query limits, the time limit, no phrase in the log, `search-check` and `reindex-search`, publication taking effect without reindexing, repeated events.

When the owner's real (non-confidential) documents and typical queries arrive, they are tested locally (never committed): upload them to a test installation and record the results in this section.

## Performance target

p95 below 500 ms on a synthetic corpus of 20,000 documents (CLAUDE.md 10). Measured in Phase 3, stage C with a generator of 20,000 and 100,000 documents (`make perf`, manual); results and query plans will be recorded here.

