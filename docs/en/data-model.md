# Data model

> Applies to: v0.0.0 (pre-release) · Last reviewed: 2026-11-12

PostgreSQL 16. Times are stored as `timestamptz` in UTC. Primary keys are UUIDs unless noted. This is the target model; the Alembic migrations are the source of truth and this page must match them.

## Table ownership

Every module owns its tables, and no module reads or writes the tables of another module directly (it calls that module's `services/api.py`). Table names carry a prefix so the owner is obvious.

| Owner | Prefix | Tables |
|---|---|---|
| core | none | `users`, `roles`, `role_permissions`, `user_roles`, `ad_group_role_map`, `sessions`, `audit_log`, `app_settings` |
| `documents` | `doc_` | `doc_categories`, `doc_documents`, `doc_versions`, `doc_email_metadata` |
| `search` | `srch_` | `srch_parts` |
| `ingestion` | `ing_` | none in the MVP: processing status and metadata live in `doc_versions` and are updated through `documents.services.api` |

A foreign key may point to a table of a module that the owner `requires` (for example `srch_parts.version_id` references `doc_versions.id` with `ON DELETE CASCADE`). Migrations: one Alembic branch per module plus a `core` branch; the branch of a module that requires another one declares `depends_on`.

## Tables

### Identity and permissions

| Table | Key columns | Notes |
|---|---|---|
| `users` | `id`, `username`, `display_name`, `email`, `source` (`local`/`ad`), `password_hash` (local only), `ad_object_guid`, `is_active`, `last_login_at` | Local users exist only for the break-glass admin |
| `roles` | `id`, `name`, `description`, `is_system` | System roles: Admin, Publisher, Reader. Not deletable |
| `role_permissions` | `role_id`, `permission_code` | Permission codes come from the catalog in code ([auth-and-permissions.md](auth-and-permissions.md)) |
| `user_roles` | `user_id`, `role_id`, `source` (`manual`/`ad`) | Effective permissions = union of all roles |
| `ad_group_role_map` | `ad_group_dn`, `role_id` | Edited by an admin in the UI |
| `sessions` | `id_hash`, `user_id`, `created_at`, `last_seen_at`, `expires_at`, `ip`, `user_agent` | Server-side, revocable |

### Documents

| Table | Key columns | Notes |
|---|---|---|
| `doc_categories` | `id`, `name`, `parent_id` | Optional tree |
| `doc_documents` | `id`, `title`, `description`, `category_id`, `owner_id`, `status` (`draft`/`published`/`archived`), `published_version_id`, `latest_version_id`, `created_at`, `updated_at`, `published_at`, `published_by`, `archived_at` | Readers see only `published` documents, through `published_version_id` |
| `doc_versions` | `id`, `document_id`, `version_no`, `kind` (`pdf`/`docx`/`doc`/`txt`/`eml`/`msg`), `original_filename`, `mime_type`, `size_bytes`, `sha256`, `storage_key`, `derived_key`, `processing_status` (`pending`/`processing`/`done`/`failed`), `processing_error`, `processing_warnings` (jsonb), `ocr_applied`, `page_count`, `uploaded_by`, `uploaded_at`, `change_note` | Unique `(document_id, version_no)` |
| `doc_email_metadata` | `version_id`, `subject`, `from_addr`, `to_addrs`, `cc_addrs`, `sent_at`, `message_id` | Only for EML/MSG |

### Search index

| Table | Key columns | Notes |
|---|---|---|
| `srch_parts` | `id`, `version_id`, `parent_part_id`, `part_type` (`page`/`body`/`attachment`), `name`, `page_no`, `chunk_no`, `language` (`pl`/`en`/`mixed`/`unknown`), `text_content`, `tsv_pl`, `tsv_en`, `tsv_simple`, `ocr_applied` | One row per PDF page, per e-mail body, per attachment chunk. `tsv_*` are generated from `text_content`. Written by `search` from the event `document.version.extracted` |

### System

| Table | Key columns | Notes |
|---|---|---|
| `audit_log` | `id` (bigserial), `at`, `user_id`, `action`, `entity_type`, `entity_id`, `ip`, `details` (jsonb) | Append-only. Actions: login, login_failed, upload, publish, unpublish, download, archive, delete, role_change, permission_change, setting_change |
| `app_settings` | `key`, `value` (jsonb), `updated_by`, `updated_at` | Non-secret settings editable in the admin UI |

## Relations

```
users ─< user_roles >─ roles ─< role_permissions
ad_group_role_map >─ roles
doc_documents ─< doc_versions ─< srch_parts (self-reference via parent_part_id)
doc_versions ─ 0..1 ─ doc_email_metadata
doc_documents >─ doc_categories
```

## Indexes

| Index | Purpose |
|---|---|
| GIN on `srch_parts.tsv_pl`, `tsv_en`, `tsv_simple` | Full-text search |
| btree `srch_parts(version_id)` | Joins |
| GIN (`gin_trgm_ops`) on `doc_documents.title`, `doc_versions.original_filename`, `doc_email_metadata.subject`, `doc_email_metadata.from_addr` | Fuzzy metadata search |
| btree `doc_versions(sha256)` | Duplicate warning |
| btree `doc_documents(status, category_id)` | Lists and filters |
| btree `audit_log(at)`, `(user_id)`, `(entity_type, entity_id)` | Audit viewer |

## Text-search objects

Created by the init SQL of the `db` image and by Alembic (`core` branch):
- extensions `pg_trgm`, `unaccent`
- dictionary `pl_hunspell`, configuration `pl` (Hunspell, then `simple` for unknown words)
- configuration `simple_unaccent`
- built-in `english`

See [search.md](search.md).

## Invariants

- At least one active user holds the Admin role.
- `doc_documents.published_version_id` always points to a version of the same document with `processing_status = done`.
- A document with status `published` has a non-null `published_version_id`.
- Original files are never modified; `sha256` of the stored file matches the column.
- `audit_log` rows are never updated or deleted by the application.

## Deletion and GDPR/RODO

- Archive = hidden from search, kept on disk.
- Hard delete (permission `documents.delete`) removes the database rows, the original file and the derived files, and writes an audit entry that contains no document text.
- Retention periods are an open question, see [decisions.md](decisions.md).
