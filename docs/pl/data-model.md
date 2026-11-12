# Model danych

> Dotyczy: v0.0.0 (przed pierwszym wydaniem) · Ostatnia aktualizacja: 2026-11-12

PostgreSQL 16. Czasy są zapisywane jako `timestamptz` w UTC. Klucze główne to UUID, chyba że napisano inaczej. To model docelowy; źródłem prawdy są migracje Alembica i ta strona musi się z nimi zgadzać.

## Tabele

### Tożsamość i uprawnienia

| Tabela | Kluczowe kolumny | Uwagi |
|---|---|---|
| `users` | `id`, `username`, `display_name`, `email`, `source` (`local`/`ad`), `password_hash` (tylko lokalni), `ad_object_guid`, `is_active`, `last_login_at` | Użytkownicy lokalni istnieją tylko dla administratora awaryjnego |
| `roles` | `id`, `name`, `description`, `is_system` | Role systemowe: Admin, Publisher, Reader. Nie można ich usunąć |
| `role_permissions` | `role_id`, `permission_code` | Kody uprawnień pochodzą z katalogu w kodzie ([auth-and-permissions.md](auth-and-permissions.md)) |
| `user_roles` | `user_id`, `role_id`, `source` (`manual`/`ad`) | Uprawnienia efektywne = suma wszystkich ról |
| `ad_group_role_map` | `ad_group_dn`, `role_id` | Edytowane przez admina w interfejsie |
| `sessions` | `id_hash`, `user_id`, `created_at`, `last_seen_at`, `expires_at`, `ip`, `user_agent` | Po stronie serwera, można unieważnić |

### Dokumenty

| Tabela | Kluczowe kolumny | Uwagi |
|---|---|---|
| `categories` | `id`, `name`, `parent_id` | Opcjonalne drzewo |
| `documents` | `id`, `title`, `description`, `category_id`, `owner_id`, `status` (`draft`/`published`/`archived`), `published_version_id`, `latest_version_id`, `created_at`, `updated_at`, `published_at`, `published_by`, `archived_at` | Czytelnicy widzą tylko dokumenty `published`, przez `published_version_id` |
| `document_versions` | `id`, `document_id`, `version_no`, `kind` (`pdf`/`docx`/`doc`/`txt`/`eml`/`msg`), `original_filename`, `mime_type`, `size_bytes`, `sha256`, `storage_key`, `derived_key`, `processing_status` (`pending`/`processing`/`done`/`failed`), `processing_error`, `processing_warnings` (jsonb), `ocr_applied`, `page_count`, `uploaded_by`, `uploaded_at`, `change_note` | Unikalne `(document_id, version_no)` |
| `document_parts` | `id`, `version_id`, `parent_part_id`, `part_type` (`page`/`body`/`attachment`), `name`, `page_no`, `chunk_no`, `language` (`pl`/`en`/`mixed`/`unknown`), `text_content`, `tsv_pl`, `tsv_en`, `tsv_simple`, `ocr_applied` | Jeden wiersz na stronę PDF, treść maila lub fragment załącznika. Kolumny `tsv_*` są generowane z `text_content` |
| `email_metadata` | `version_id`, `subject`, `from_addr`, `to_addrs`, `cc_addrs`, `sent_at`, `message_id` | Tylko dla EML/MSG |

### System

| Tabela | Kluczowe kolumny | Uwagi |
|---|---|---|
| `audit_log` | `id` (bigserial), `at`, `user_id`, `action`, `entity_type`, `entity_id`, `ip`, `details` (jsonb) | Tylko dopisywanie. Akcje: login, login_failed, upload, publish, unpublish, download, archive, delete, role_change, permission_change, setting_change |
| `app_settings` | `key`, `value` (jsonb), `updated_by`, `updated_at` | Ustawienia niesekretne, edytowane w panelu admina |

## Relacje

```
users ─< user_roles >─ roles ─< role_permissions
ad_group_role_map >─ roles
documents ─< document_versions ─< document_parts (samoodniesienie przez parent_part_id)
document_versions ─ 0..1 ─ email_metadata
documents >─ categories
```

## Indeksy

| Indeks | Cel |
|---|---|
| GIN na `document_parts.tsv_pl`, `tsv_en`, `tsv_simple` | Wyszukiwanie pełnotekstowe |
| btree `document_parts(version_id)` | Złączenia |
| GIN (`gin_trgm_ops`) na `documents.title`, `document_versions.original_filename`, `email_metadata.subject`, `email_metadata.from_addr` | Wyszukiwanie przybliżone po metadanych |
| btree `document_versions(sha256)` | Ostrzeżenie o duplikacie |
| btree `documents(status, category_id)` | Listy i filtry |
| btree `audit_log(at)`, `(user_id)`, `(entity_type, entity_id)` | Przeglądarka audytu |

## Obiekty wyszukiwania tekstowego

Tworzone przez init SQL obrazu `db` oraz przez Alembic:
- rozszerzenia `pg_trgm`, `unaccent`
- słownik `pl_hunspell`, konfiguracja `pl` (Hunspell, potem `simple` dla słów nieznanych)
- konfiguracja `simple_unaccent`
- wbudowana `english`

Zobacz [search.md](search.md).

## Niezmienniki

- Co najmniej jeden aktywny użytkownik ma rolę Admin.
- `documents.published_version_id` zawsze wskazuje wersję tego samego dokumentu z `processing_status = done`.
- Dokument o statusie `published` ma niepuste `published_version_id`.
- Pliki oryginalne nigdy nie są modyfikowane; `sha256` zapisanego pliku zgadza się z kolumną.
- Aplikacja nigdy nie aktualizuje ani nie usuwa wierszy `audit_log`.

## Usuwanie i RODO

- Archiwizacja = ukrycie w wyszukiwarce, plik zostaje na dysku.
- Trwałe usunięcie (uprawnienie `documents.delete`) kasuje wiersze w bazie, plik oryginalny i pliki pochodne oraz zapisuje wpis audytowy, który nie zawiera treści dokumentu.
- Okresy retencji to otwarte pytanie, zobacz [decisions.md](decisions.md).
