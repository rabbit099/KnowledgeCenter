# Module: documents

> Applies to: v0.4.0 · Last reviewed: 2026-11-20

## Scope

Owns documents, their versions, categories and the lifecycle (draft → published → archived), the storage of original and derived files, downloads and the document visibility rule. Upload validation belongs here; text extraction does not (see [ingestion](ingestion.md)).

## Dependencies

Requires no other module. Required by [ingestion](ingestion.md) and [search](search.md).

## Permissions

`documents.read`, `documents.upload`, `documents.publish`, `documents.publish_any`, `documents.read_drafts`, `documents.edit_any`, `documents.delete`, `categories.manage`. Meaning of each code: [auth-and-permissions.md](../auth-and-permissions.md).

## Tables

`doc_categories`, `doc_documents`, `doc_versions` (including `processing_status` and processing metadata), `doc_email_metadata`. See [data-model.md](../data-model.md).

## Events

- Publishes `document.version.uploaded` (`document_id`, `version_id`) after a new version is stored.
- Publishes `document.published` (`document_id`, `version_id`) after a document is published (Phase 3, after the commit). No subscribers yet: search reads the visibility live from `documents`, so publishing, archiving and restoring need no reindex.
- Subscribes to nothing.

## Public interface

`modules/documents/services/api.py`, the only part other modules may import. Implemented (Phase 2, stage A; Phase 3): `visibility_clause(user)` (the SQL condition for every query, `visibility.py`: own documents in every state for the owner, drafts for `documents.read_drafts`, published for `documents.read`, archived for `documents.edit_any`), `can_see_authors(user)` (who sees authors of other people's documents, ADR-030), `list_documents(..., owner_id=)` (author filter for them), `Document.current_version_id()` (published version of published and archived documents, latest of drafts), the status constants `STATUS_DRAFT`, `STATUS_PUBLISHED`, `STATUS_ARCHIVED`, `get_visible_document`, `versions_of`, `list_documents`, `visible_duplicates` (only documents the user may see). For `ingestion` (stage B): `claim`, `finish`, `fail`, `retry_later`, `reset_for_reprocess`, `recover_stuck`, `ids_with_status` (processing status, each transition under a row lock and only for the attempt that claimed the version, see `processing.py`), `save_email_metadata`, `apply_subject_title` (e-mail subject as the default title), `email_metadata_of`, `get_visible_version`, `can_reprocess(user, document)`, `original_path(storage_dir, key)`, and the upload checks `sanitize_filename` and `detect_kind` (also used for e-mail attachments). Later: save a derived file (Phase 4).

## Extension points

File storage: originals and derived files are accessed through a storage interface (local disk first), so another backend can be added without touching the callers.

## Status

Phase 2, stage A: tables `doc_documents`, `doc_versions`, `doc_email_metadata` (`documents_0002`); storage on the `storage` volume; upload with type detection by content (ADR-021), size limit, sanitised file names and SHA-256; drafts with visibility in SQL; pages "Moje dokumenty" (`/documents`, permission `documents.upload` or `documents.read_drafts`), "Wgraj dokument" and the document page; duplicate warning; audit `document.upload`; command `baza-wiedzy storage-cleanup`. Stage B: processing status transitions and e-mail metadata for `ingestion`; the document page loads the extracted parts from `ingestion` when that module is enabled. Phase 3, stage A (ADR-025): lifecycle actions on the document page — Publikuj, Cofnij publikację, Archiwizuj, Przywróć (`lifecycle.py`, `POST /documents/<id>/lifecycle`, audited `document.publish|unpublish|archive|restore`), columns `published_at`, `published_by`, `archived_at` and the check "published needs a published version" (`documents_0004`), the owner sees own documents in every state, a status column in "Moje dokumenty", a warning when the content is not (fully) searchable; download of the original (`download.py`, `GET /documents/<id>/download`, audited `document.download`); `documents.publish_any` and `documents.edit_any` require `documents.read_drafts` in a role. User versions, categories, duplicate handling and preview come in Phase 5.
