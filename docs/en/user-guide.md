# Search — guide for users and administrators

> Applies to: v0.4.0 · Last reviewed: 2026-11-20

How to find documents, what search can and cannot do, who sees what, and what the administrator runs. Technical details (indexes, ranking, measurements): [search.md](search.md).

## For users

### Searching

The search field is on the start page and under "Szukaj". Type at least 2 and at most 200 characters and press Enter.

| Type | Finds |
|---|---|
| `umowa serwisowa` | documents containing **all** the words, in any form ("umowy serwisowej", "umowie") |
| `"zasady bezpieczeństwa"` | the exact phrase: the words next to each other in this order |
| `faktura -korekta` | documents with "faktura" and **without** "korekta" anywhere in the document |
| `faktura or rachunek` | documents with either word (`or` in English, lower case) |

Upper and lower case do not matter. Very common words ("i", "w", "the") do not help: they are in almost every document, so a query made only of them finds nearly everything; add a specific word.

### Inflection and languages

Polish and English words are found in every form: `umowa` finds "umowy", "umowie", "umów"; `invoice` finds "invoices"; `Łódź` finds "w Łodzi". A document may be in both languages; each fragment is searched in both. Names and words missing from the dictionary are found in the form written in the text.

### Numbers, codes and addresses

Numbers and codes are found with or without separators:

| Type | Also finds |
|---|---|
| `7251801126` or `725-18-01-126` | the tax number written either way |
| `17/2026` | "UM/17/2026" (a part between separators) |
| `600 100 200`, `600100200`, `+48 600 100 200` | the same phone number written any of these ways |
| `IT002` or `IT-002`, `EX-PVE0` | codes and server names |
| `example.com`, `ksiegowosc@example.com` | addresses in that domain, the exact address |
| `192.168.10.25`, `192.168.10` | the address, addresses in that network |

Numbers must be exact: `FV/2026/11/0042` never finds `…0043`.

### Results

Results are grouped by document, best first (a match in the title or e-mail subject ranks above one in the file name or sender, which ranks above one in the text). Each result shows:
- the title (a link to the document page, where the file can be downloaded);
- type, file name, date (e-mail: the sending date, otherwise the upload date) and, for users who work with other people's documents, the author;
- where the best match is ("strona 7", "załącznik: umowa.pdf › strona 2") and a fragment with the matching words highlighted; "Dopasowanie w tytule, nazwie pliku albo nagłówkach maila" when only those matched;
- "szkic" or "zarchiwizowany" when the document is not published.

The page says how many documents were found; 20 per page, page links at the bottom, at most 50 pages (make the query more specific to see beyond).

### Filters and sorting

Under "Filtry i sortowanie": file type (PDF, Word, e-mail, text), language of the text, date from/to, "Pokaż zarchiwizowane" (only for those who may see archived documents), sorting by relevance or by date (newest first). The author filter is shown only to users who work with other people's documents (Admin and roles that publish or edit other people's documents). Every filter is part of the page address, so a search can be bookmarked or sent to a colleague (who sees only what they are allowed to see).

### What search cannot do

- **No synonyms**: "kopia zapasowa" does not find "backup", "umowa" does not find "kontrakt". Try the other word, or both: `"kopia zapasowa" or backup`.
- **Without Polish letters only the exact form**: `lodz` finds "Łódź" but not "w Łodzi" (`lodzi` does). Type Polish letters to find every form.
- **No partial words or numbers**: `faktu` does not find "faktura" in the text; `7251` does not find "7251801126"; `1002` does not find "600 100 200". Small typos and incomplete words are forgiven only in titles, file names, e-mail subjects and senders (never in numbers).
- **Scanned pages** are found only after OCR (Phase 4; the document page then says "wymaga OCR"). Documents whose processing failed and documents without text are found only by title and file name.
- **Limits**: 2 to 200 characters; 20 results per page; at most 50 pages; a search that takes longer than 3 seconds is shown by date instead of relevance with a note ("Zapytanie jest bardzo ogólne…"); if even that is too slow, the page asks to narrow the query.

### Who sees what

| Document | Found by |
|---|---|
| Published | everyone with the permission to read documents (every default role) |
| Draft | its author and holders of `documents.read_drafts` (Admin by default); marked "szkic" |
| Archived | its author and holders of `documents.edit_any` (Admin), only with "Pokaż zarchiwizowane"; never Readers |

Search applies the same rules as the document pages: a document you cannot open never appears in results, also not in the number of results.

### Privacy of your searches

What you type is not stored, not written to any server log and not recorded in the audit log. The server writes only a line with numbers (how many results, how long it took). Your browser keeps searches in its own history, like any visited page.

## For administrators

### Search operations

Commands in the application container (`docker compose exec app baza-wiedzy …`):

| Command | When | Expected output |
|---|---|---|
| `search-check` | after an update, or when a document is not found | `Search index OK.` (exit 0), or the numbers of versions not indexed / different / orphaned (identifiers only) and a hint (exit 1) |
| `reindex-search --missing` | `search-check` reported differences | `Indexed N version(s); removed M orphaned; took T s.` |
| `reindex-search --all` | after a dictionary, configuration or identifier-rule change (the changelog says so) | the same line; about 5 minutes per 20,000 documents |
| `reindex-search --document ID` | one document | the same line |

New documents are indexed right after processing; publishing, archiving and restoring need no reindex. After an update, one worker process indexes what is missing at its start (`search: indexed N version(s) missing from the index`); the other processes skip (`…runs in another worker process; skipped here`).

### Local tools (development machine only)

- `make relevance DIR=<documents> QUERIES=<file> AS=<user> [ONLY=1]` uploads your own documents as drafts to the **local development stack** and prints the first results of each query for a manual rating. **The output contains document text**: never commit it, attach it to tickets or paste it anywhere public. The repository is public: keep the documents and the query file outside it (the command refuses paths inside it). Details: [search.md](search.md#golden-tests).
- `make perf N=20000` measures search on synthetic documents (no real data); results in [search.md](search.md#performance-target).

### Users and access

- Who sees authors: holders of `documents.read_drafts`, `documents.publish_any` or `documents.edit_any` (ADR-030). A role editor that gives one of the latter two also requires `documents.read_drafts`.
- Accounts: `baza-wiedzy users list` and `baza-wiedzy users set-password <login>` (local accounts only); see [auth-and-permissions.md](auth-and-permissions.md#local-break-glass-admin).

### Privacy checks

Search phrases, file names and e-mail subjects never reach logs, the audit log or error messages (ADR-025). `make smoke` checks it on every release; on a server: search for a unique word, then `docker compose logs proxy app worker db | grep -c <word>` must print `0`.
