# Module: documents

> Applies to: v0.0.0 (pre-release) · Last reviewed: 2026-11-12

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

`modules/documents/services/api.py`, the only part other modules may import. Planned content (not implemented yet): read a version, update processing status and metadata, store e-mail metadata, open an original file and save a derived file, and the SQL fragment that implements document visibility for `search`.

## Extension points

File storage: originals and derived files are accessed through a storage interface (local disk first), so another backend can be added without touching the callers.

## Status

Phase 0: manifest, permissions, a menu entry and an empty public interface. Implementation comes in Phase 2 (upload) and Phase 5 (lifecycle, versions, categories).
