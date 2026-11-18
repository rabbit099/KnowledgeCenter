# Data model

> Applies to: v0.3.0 · Last reviewed: 2026-11-17

PostgreSQL 16. Times are stored as `timestamptz` in UTC. Primary keys are UUIDs unless noted. This is the target model; the Alembic migrations are the source of truth and this page must match them.

## Table ownership

Every module owns its tables, and no module reads or writes the tables of another module directly (it calls that module's `services/api.py`). Table names carry a prefix so the owner is obvious.

| Owner | Prefix | Tables |
|---|---|---|
| core | none | `users`, `roles`, `role_permissions`, `user_roles`, `ad_group_role_map`, `sessions`, `audit_log`, `app_settings` |
| `documents` | `doc_` | `doc_categories`, `doc_documents`, `doc_versions`, `doc_email_metadata` |
| `search` | `srch_` | `srch_parts` |
| `ingestion` | `ing_` | `ing_parts`, `ing_results`, `ing_outbox` (the processing status itself lives in `doc_versions` and is updated through `documents.services.api`) |

A foreign key may point to a table of a module that the owner `requires` (for example `srch_parts.version_id` references `doc_versions.id` with `ON DELETE CASCADE`). Migrations: one Alembic branch per module plus a `core` branch; the branch of a module that requires another one declares `depends_on`. Revision files live in the package named by the manifest's `migrations_path` (`src/baza_wiedzy/core/db/migrations/`, `src/baza_wiedzy/modules/<name>/migrations/`) and are named `<branch>_<NNNN>_<slug>.py` with revision id `<branch>_<NNNN>`; `alembic/env.py` and `alembic.ini` are shared. Policy: `baza-wiedzy migrate` upgrades **all** branches (core and every module in the repository), independent of `ENABLED_MODULES`, so the schema is always complete: enabling a module later needs no migration, and a module's tables always exist for foreign keys of other modules. `/ready` compares the database with all script heads. The cost is a few empty tables of disabled modules, which is negligible. Create a revision with `make revision branch=<branch> m="text"`. Current revisions: `core_0001` (extensions), `core_0002` (identity tables and system roles), `core_0003` (audit log), `documents_0001` (default grants of `documents.*`, `depends_on` `core_0002`), `documents_0002` (`doc_documents`, `doc_versions`, `doc_email_metadata`), `documents_0003` (`doc_documents.title_source`), `ingestion_0001` (`ing_parts`, `ing_results`, `ing_outbox`, `depends_on` `documents_0002`), `search_0001` (dictionary and text-search configurations, `depends_on` `core_0001`). `alembic_version` stores only the heads; revisions implied by `depends_on` are not listed. SQLAlchemy models (`core/db/models.py`) must match the migrations; `make test-db` compares them.

## Tables

### Identity and permissions

| Table | Key columns | Notes |
|---|---|---|
| `users` | `id` (uuid), `username`, `display_name`, `email`, `source` (`local`/`ad`), `password_hash` (local only), `ad_object_guid`, `ad_dn`, `is_active`, `created_at`, `updated_at`, `last_login_at`, `password_changed_at` | `username` is unique ignoring case (`lower(username)`), across local and AD accounts. A check enforces: local ⇔ password hash present. Local users exist only for the break-glass admin (CLI). AD users are created at first login or in advance by an admin (no `ad_object_guid` until the first login links them) |
| `roles` | `id`, `name` (unique ignoring case), `description`, `system_key` (`admin`/`publisher`/`reader` or NULL), `created_at`, `updated_at` | The three system roles are created by `core_0002`; code recognises them by `system_key`, so renaming is harmless |
| `role_permissions` | `role_id`, `permission_code` | Permission codes come from the catalog in code ([auth-and-permissions.md](auth-and-permissions.md)). **Admin has no rows**: it always holds the full catalog, computed in code. Default grants of module permissions come from the module's migration (`documents_0001`: Reader `documents.read`; Publisher `documents.read`, `documents.upload`, `documents.publish`) |
| `user_roles` | `user_id`, `role_id`, `source` (`manual`/`ad`), `created_at` | Primary key `(user_id, role_id, source)`. Effective permissions = union of all roles. Rows with `source = ad` are recalculated at each AD login |
| `ad_group_role_map` | `id`, `ad_group_dn`, `role_id`, `created_at`, `created_by` | Unique `(lower(ad_group_dn), role_id)`. Edited by an admin in the UI |
| `sessions` | `id_hash` (SHA-256 of the cookie token), `user_id`, `created_at`, `last_seen_at`, `expires_at` (absolute end), `ip`, `user_agent` | Server-side, revocable (revocation deletes the row). The token itself is never stored |

### Documents

| Table | Key columns | Notes |
|---|---|---|
| `doc_categories` | `id`, `name`, `parent_id` | Optional tree |
| `doc_documents` | `id`, `title`, `title_source` (`user`/`filename`/`subject`, `documents_0003`), `description`, `owner_id` → `users`, `status` (`draft`/`published`/`archived`), `latest_version_id`, `published_version_id`, `created_at`, `updated_at` | Created by `documents_0002` (Phase 2: every upload is a `draft` with version 1). Planned for Phase 5: `category_id`, `published_at`, `published_by`, `archived_at`. Visibility in SQL: `documents.services.api.visibility_clause` |
| `doc_versions` | `id`, `document_id`, `version_no`, `kind` (`pdf`/`docx`/`doc`/`txt`/`eml`/`msg`), `original_filename` (sanitised, metadata only), `size_bytes`, `sha256`, `storage_key` (unique, random UUID), `uploaded_by` → `users`, `uploaded_at`, `processing_status` (`pending`/`processing`/`done`/`failed`/`needs_ocr`), `processing_error` (message for the user), `processing_attempts`, `processing_started_at`, `processed_at`, `page_count`, `ocr_applied` | Unique `(document_id, version_no)`; indexes on `sha256` (duplicate warning) and `processing_status`. Planned: `derived_key` (Phase 4), `change_note` (Phase 5) |
| `doc_email_metadata` | `version_id`, `subject`, `from_addr`, `to_addrs`, `cc_addrs`, `sent_at`, `message_id` | Only for EML/MSG (top-level message); filled by `ingestion` through `documents.services.api`, damaged values stay empty |

### Ingestion

| Table | Key columns | Notes |
|---|---|---|
| `ing_parts` | `id` (bigint identity), `version_id` → `doc_versions` (`ON DELETE CASCADE`), `seq`, `part_type` (`page`/`body`/`attachment`), `origin` (jsonb path, e.g. `[{"type": "attachment", "name": "umowa.docx"}]`), `page_no`, `chunk_no`, `language` (`pl`/`en`/`unknown`), `text`, `char_count`, `needs_ocr`, `created_at` | Extracted text, at most 100,000 characters per row (ADR-020). Unique `(version_id, seq)`. Replaced as a whole in one transaction when a version is processed again |
| `ing_results` | `version_id` (PK, → `doc_versions`, cascade), `notes` (jsonb list: `encoding`, `skipped`, `failed`, `fallback` with origin), `parts_count`, `chars_total`, `finished_at` | Notes are shown on the document page |
| `ing_outbox` | `id` (bigint identity), `event`, `payload` (jsonb, identifiers only), `created_at`, `delivered_at`, `attempts`, `last_error` (exception class) | Partial index on undelivered rows; delivered rows removed after `OUTBOX_RETENTION_DAYS` |

### Search index

| Table | Key columns | Notes |
|---|---|---|
| `srch_parts` | `id`, `version_id`, `parent_part_id`, `part_type` (`page`/`body`/`attachment`), `name`, `page_no`, `chunk_no`, `language` (`pl`/`en`/`mixed`/`unknown`), `text_content`, `tsv_pl`, `tsv_en`, `tsv_simple`, `ocr_applied` | Planned (Phase 3). One row per PDF page, per e-mail body, per attachment chunk. `tsv_*` are generated from `text_content`. Copied by `search` from `ing_parts` through `ingestion.services.api` after `document.version.extracted` (ADR-020) |

### System

| Table | Key columns | Notes |
|---|---|---|
| `audit_log` | `id` (bigint identity), `at`, `actor_user_id`, `actor_label` (user name at the time, or `cli`), `action`, `entity_type`, `entity_id`, `ip`, `details` (jsonb) | Append-only: a trigger rejects `UPDATE`, `DELETE` and `TRUNCATE` (`core_0003`). No foreign keys, so entries outlive the rows they describe. Action names: [auth-and-permissions.md](auth-and-permissions.md#audit-log). Known limitation: the application role owns the table (it runs the migrations), so someone using that database role directly could disable the trigger; the protection is against the application, not against a database administrator. Separate database roles are planned for Phase 7 |
| `app_settings` | `key`, `value` (jsonb), `updated_by`, `updated_at` | Planned, not created yet: non-secret settings editable in the admin UI |

## Relations

```
users ─< user_roles >─ roles ─< role_permissions
ad_group_role_map >─ roles
users ─< sessions
doc_documents ─< doc_versions ─< ing_parts, ing_results (cascade)
doc_versions ─< srch_parts (Phase 3)
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
| btree `audit_log(at)`, `(actor_user_id)`, `(action)`, `(entity_type, entity_id)` | Audit viewer |
| unique `users(lower(username))`, `users(ad_object_guid)`, `roles(lower(name))`, `roles(system_key)` | Case-insensitive names, AD identity |
| btree `sessions(user_id)`, `sessions(expires_at)`, `user_roles(role_id)` | Revocation, cleanup of expired sessions, role usage |

## Text-search objects

Created only by Alembic migrations, in schema `public`, as the non-superuser application role (the `db` image ships only the dictionary files):
- extensions `pg_trgm`, `unaccent` (`core_0001`)
- dictionary `pl_hunspell` (`search_0001`), configuration `pl` (Hunspell, then `simple` for unknown words)
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
