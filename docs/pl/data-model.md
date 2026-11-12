# Model danych

> Dotyczy: v0.1.0 · Ostatnia aktualizacja: 2026-11-12

PostgreSQL 16. Czasy są zapisywane jako `timestamptz` w UTC. Klucze główne to UUID, chyba że napisano inaczej. To model docelowy; źródłem prawdy są migracje Alembica i ta strona musi się z nimi zgadzać.

## Własność tabel

Każdy moduł jest właścicielem swoich tabel i żaden moduł nie czyta ani nie zapisuje bezpośrednio tabel innego modułu (wywołuje jego `services/api.py`). Nazwy tabel mają prefiks, żeby właściciel był oczywisty.

| Właściciel | Prefiks | Tabele |
|---|---|---|
| rdzeń | brak | `users`, `roles`, `role_permissions`, `user_roles`, `ad_group_role_map`, `sessions`, `audit_log`, `app_settings` |
| `documents` | `doc_` | `doc_categories`, `doc_documents`, `doc_versions`, `doc_email_metadata` |
| `search` | `srch_` | `srch_parts` |
| `ingestion` | `ing_` | brak w MVP: status przetwarzania i metadane są w `doc_versions` i aktualizowane przez `documents.services.api` |

Klucz obcy może wskazywać tabelę modułu, którego właściciel `requires` (na przykład `srch_parts.version_id` wskazuje `doc_versions.id` z `ON DELETE CASCADE`). Migracje: jedna gałąź Alembica na moduł oraz gałąź `core`; gałąź modułu, który wymaga innego, deklaruje `depends_on`. Pliki rewizji leżą w pakiecie wskazanym przez `migrations_path` w manifeście (`src/baza_wiedzy/core/db/migrations/`, `src/baza_wiedzy/modules/<nazwa>/migrations/`) i mają nazwy `<gałąź>_<NNNN>_<opis>.py` z identyfikatorem rewizji `<gałąź>_<NNNN>`; `alembic/env.py` i `alembic.ini` są wspólne. Zasada: `baza-wiedzy migrate` aktualizuje **wszystkie** gałęzie (rdzeń i każdy moduł w repozytorium), niezależnie od `ENABLED_MODULES`, więc schemat jest zawsze kompletny: późniejsze włączenie modułu nie wymaga migracji, a tabele modułu zawsze istnieją dla kluczy obcych innych modułów. `/ready` porównuje bazę ze wszystkimi głowami skryptów. Kosztem jest kilka pustych tabel wyłączonych modułów, co jest pomijalne. Nową rewizję tworzy `make revision branch=<gałąź> m="tekst"`. Obecne rewizje: `core_0001` (rozszerzenia), `search_0001` (słownik i konfiguracje wyszukiwania tekstowego, `depends_on` `core_0001`). `alembic_version` przechowuje tylko głowy, więc po pełnej migracji zawiera `search_0001`; `core_0001` wynika z `depends_on`.

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
| `doc_categories` | `id`, `name`, `parent_id` | Opcjonalne drzewo |
| `doc_documents` | `id`, `title`, `description`, `category_id`, `owner_id`, `status` (`draft`/`published`/`archived`), `published_version_id`, `latest_version_id`, `created_at`, `updated_at`, `published_at`, `published_by`, `archived_at` | Czytelnicy widzą tylko dokumenty `published`, przez `published_version_id` |
| `doc_versions` | `id`, `document_id`, `version_no`, `kind` (`pdf`/`docx`/`doc`/`txt`/`eml`/`msg`), `original_filename`, `mime_type`, `size_bytes`, `sha256`, `storage_key`, `derived_key`, `processing_status` (`pending`/`processing`/`done`/`failed`), `processing_error`, `processing_warnings` (jsonb), `ocr_applied`, `page_count`, `uploaded_by`, `uploaded_at`, `change_note` | Unikalne `(document_id, version_no)` |
| `doc_email_metadata` | `version_id`, `subject`, `from_addr`, `to_addrs`, `cc_addrs`, `sent_at`, `message_id` | Tylko dla EML/MSG |

### Indeks wyszukiwania

| Tabela | Kluczowe kolumny | Uwagi |
|---|---|---|
| `srch_parts` | `id`, `version_id`, `parent_part_id`, `part_type` (`page`/`body`/`attachment`), `name`, `page_no`, `chunk_no`, `language` (`pl`/`en`/`mixed`/`unknown`), `text_content`, `tsv_pl`, `tsv_en`, `tsv_simple`, `ocr_applied` | Jeden wiersz na stronę PDF, treść maila lub fragment załącznika. Kolumny `tsv_*` są generowane z `text_content`. Zapisywana przez `search` ze zdarzenia `document.version.extracted` |

### System

| Tabela | Kluczowe kolumny | Uwagi |
|---|---|---|
| `audit_log` | `id` (bigserial), `at`, `user_id`, `action`, `entity_type`, `entity_id`, `ip`, `details` (jsonb) | Tylko dopisywanie. Akcje: login, login_failed, upload, publish, unpublish, download, archive, delete, role_change, permission_change, setting_change |
| `app_settings` | `key`, `value` (jsonb), `updated_by`, `updated_at` | Ustawienia niesekretne, edytowane w panelu admina |

## Relacje

```
users ─< user_roles >─ roles ─< role_permissions
ad_group_role_map >─ roles
doc_documents ─< doc_versions ─< srch_parts (samoodniesienie przez parent_part_id)
doc_versions ─ 0..1 ─ doc_email_metadata
doc_documents >─ doc_categories
```

## Indeksy

| Indeks | Cel |
|---|---|
| GIN na `srch_parts.tsv_pl`, `tsv_en`, `tsv_simple` | Wyszukiwanie pełnotekstowe |
| btree `srch_parts(version_id)` | Złączenia |
| GIN (`gin_trgm_ops`) na `doc_documents.title`, `doc_versions.original_filename`, `doc_email_metadata.subject`, `doc_email_metadata.from_addr` | Wyszukiwanie przybliżone po metadanych |
| btree `doc_versions(sha256)` | Ostrzeżenie o duplikacie |
| btree `doc_documents(status, category_id)` | Listy i filtry |
| btree `audit_log(at)`, `(user_id)`, `(entity_type, entity_id)` | Przeglądarka audytu |

## Obiekty wyszukiwania tekstowego

Tworzone wyłącznie przez migracje Alembic, w schemacie `public`, jako zwykły użytkownik aplikacji (obraz `db` zawiera tylko pliki słownika):
- rozszerzenia `pg_trgm`, `unaccent` (`core_0001`)
- słownik `pl_hunspell` (`search_0001`), konfiguracja `pl` (Hunspell, potem `simple` dla słów nieznanych)
- konfiguracja `simple_unaccent`
- wbudowana `english`

Zobacz [search.md](search.md).

## Niezmienniki

- Co najmniej jeden aktywny użytkownik ma rolę Admin.
- `doc_documents.published_version_id` zawsze wskazuje wersję tego samego dokumentu z `processing_status = done`.
- Dokument o statusie `published` ma niepuste `published_version_id`.
- Pliki oryginalne nigdy nie są modyfikowane; `sha256` zapisanego pliku zgadza się z kolumną.
- Aplikacja nigdy nie aktualizuje ani nie usuwa wierszy `audit_log`.

## Usuwanie i RODO

- Archiwizacja = ukrycie w wyszukiwarce, plik zostaje na dysku.
- Trwałe usunięcie (uprawnienie `documents.delete`) kasuje wiersze w bazie, plik oryginalny i pliki pochodne oraz zapisuje wpis audytowy, który nie zawiera treści dokumentu.
- Okresy retencji to otwarte pytanie, zobacz [decisions.md](decisions.md).
