# Decisions, assumptions and open questions

> Applies to: v0.0.0 (pre-release) · Last reviewed: 2026-11-12

Decision log (ADR). New decisions are appended; an old decision is never rewritten, only superseded by a new entry.

## Decisions

### ADR-001 — Server-rendered UI with FastAPI + Jinja2 + HTMX
Status: accepted. A small internal tool maintained by one person. A server-rendered UI needs no separate frontend build and keeps all code in Python. Consequence: highly interactive features (for example in-browser editing) would be costly; they are out of scope.

### ADR-002 — PostgreSQL full-text search as the search engine
Status: accepted. One database for data, permissions and the index; visibility rules are applied in the same SQL; fine for tens of thousands of documents. Consequence: Polish inflection depends on a Hunspell dictionary (ADR-003). Search sits behind a `SearchBackend` interface so Meilisearch can replace it.

### ADR-003 — Three text-search configurations per text part
Status: accepted. `pl` (Hunspell + simple fallback), `english`, `simple_unaccent`; all indexed for every part, language detection is informational. Mixed-language documents and codes/names work. Cost: three tsvector columns and indexes (acceptable at this scale). Subject to the Phase 0 spike; fallbacks listed in [search.md](search.md).

### ADR-004 — Dramatiq + Redis for background jobs
Status: accepted. OCR and parsing are CPU-heavy and sync; Dramatiq's process-based workers fit. Redis holds only the queue.

### ADR-005 — pypdfium2 instead of PyMuPDF for PDF text
Status: accepted. PyMuPDF is AGPL-licensed; pypdfium2 is permissively licensed and sufficient for text extraction. Revisit only with legal approval.

### ADR-006 — OCRmyPDF + Tesseract with `pol+eng`
Status: accepted. Produces a searchable PDF and skips pages that already have text. OCR is in the MVP.

### ADR-007 — Server-side sessions in cookies, not JWT
Status: accepted. Sessions are revocable and simple with server-rendered pages and HTMX.

### ADR-008 — Active Directory through LDAPS with just-in-time users
Status: accepted. Groups map to roles in the admin UI. A local break-glass admin remains. SSO/Kerberos is a possible later step.

### ADR-009 — Explicit migration step in the update script
Status: accepted. Migrations run in a one-off container from `update.sh`, not implicitly at app start. A failed migration is a clear, single failure point. The app refuses to become ready if the schema version is behind.

### ADR-010 — Three images: app, worker, db
Status: accepted. The worker image is large (Tesseract, LibreOffice); the app image stays small. The db image carries the Hunspell dictionary.

### ADR-011 — Synchronous SQLAlchemy 2 with psycopg 3
Status: accepted. At most ~100 users; simpler code and one data-access style shared by `app` and `worker`.

### ADR-012 — Updates are performed from the host, not from the web UI
Status: accepted. Giving the web app control over Docker would be a serious security risk. The UI may show "new version available" later.

## Assumptions

- "On start about 200" (answer to the question about expected size) is read as roughly 200 documents. Design target is 20,000 documents and 100 users until confirmed.
- Server sizing is not known; resource limits are configurable.
- UI is Polish only; strings go through gettext catalogs.
- Only Polish and English documents are expected.

## Open questions

1. Can a Publisher publish documents uploaded by others? Default: no, only their own (`documents.publish_any` exists for the rest).
2. Retention: how long are archived documents and audit entries kept (GDPR/RODO)?
3. TLS certificate: internal CA or Let's Encrypt?
4. Backup destination (NAS, second server, object storage) and monitoring integration.
5. Should Readers also see archived documents? Default: no.
6. Is a virus scan (ClamAV) required by company policy? Default: optional profile, off.
7. Is the "about 200" figure documents or users?
