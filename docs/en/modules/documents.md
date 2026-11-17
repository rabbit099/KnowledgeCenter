# Module: documents

> Applies to: v0.3.0 · Last reviewed: 2026-11-17

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
- Publishes `document.published` (`document_id`, `version_id`) when a version becomes visible to readers. No subscribers in the MVP.
- Subscribes to nothing.

## Public interface

`modules/documents/services/api.py`, the only part other modules may import. Implemented (Phase 2, stage A): `visibility_clause(user)` (the SQL condition for every query: drafts for the owner and `documents.read_drafts`, published for `documents.read`, archived for `documents.edit_any`), `get_visible_document`, `versions_of`, `list_documents`, `visible_duplicates` (only documents the user may see). For `ingestion` (stage B): `claim`, `finish`, `fail`, `retry_later`, `reset_for_reprocess`, `recover_stuck`, `ids_with_status` (processing status, each transition under a row lock and only for the attempt that claimed the version, see `processing.py`), `save_email_metadata`, `apply_subject_title` (e-mail subject as the default title), `email_metadata_of`, `get_visible_version`, `can_reprocess(user, document)`, `original_path(storage_dir, key)`, and the upload checks `sanitize_filename` and `detect_kind` (also used for e-mail attachments). Later: save a derived file (Phase 4).

## Extension points

File storage: originals and derived files are accessed through a storage interface (local disk first), so another backend can be added without touching the callers.

## Status

Phase 2, stage A: tables `doc_documents`, `doc_versions`, `doc_email_metadata` (`documents_0002`); storage on the `storage` volume; upload with type detection by content (ADR-021), size limit, sanitised file names and SHA-256; drafts with visibility in SQL; pages "Moje dokumenty" (`/documents`, permission `documents.upload` or `documents.read_drafts`), "Wgraj dokument" and the document page; duplicate warning; audit `document.upload`; command `baza-wiedzy storage-cleanup`. Stage B: processing status transitions and e-mail metadata for `ingestion`; the document page loads the extracted parts from `ingestion` when that module is enabled. Lifecycle, user versions and categories come in Phase 5; downloads later.
