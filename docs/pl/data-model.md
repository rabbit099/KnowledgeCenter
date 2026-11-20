# Model danych

> Dotyczy: v0.4.0 · Ostatnia aktualizacja: 2026-11-20

PostgreSQL 16. Czasy są zapisywane jako `timestamptz` w UTC. Klucze główne to UUID, chyba że napisano inaczej. To model docelowy; źródłem prawdy są migracje Alembica i ta strona musi się z nimi zgadzać.

## Własność tabel

Każdy moduł jest właścicielem swoich tabel i żaden moduł nie czyta ani nie zapisuje bezpośrednio tabel innego modułu (wywołuje jego `services/api.py`). Nazwy tabel mają prefiks, żeby właściciel był oczywisty.

| Właściciel | Prefiks | Tabele |
|---|---|---|
| rdzeń | brak | `users`, `roles`, `role_permissions`, `user_roles`, `ad_group_role_map`, `sessions`, `audit_log`, `app_settings` |
| `documents` | `doc_` | `doc_categories`, `doc_documents`, `doc_versions`, `doc_email_metadata` |
| `search` | `srch_` | `srch_parts`, `srch_documents` |
| `ingestion` | `ing_` | `ing_parts`, `ing_results`, `ing_outbox` (sam status przetwarzania jest w `doc_versions` i aktualizowany przez `documents.services.api`) |

Klucz obcy może wskazywać tabelę modułu, którego właściciel `requires` (na przykład `srch_parts.version_id` wskazuje `doc_versions.id` z `ON DELETE CASCADE`). Migracje: jedna gałąź Alembica na moduł oraz gałąź `core`; gałąź modułu, który wymaga innego, deklaruje `depends_on`. Pliki rewizji leżą w pakiecie wskazanym przez `migrations_path` w manifeście (`src/baza_wiedzy/core/db/migrations/`, `src/baza_wiedzy/modules/<nazwa>/migrations/`) i mają nazwy `<gałąź>_<NNNN>_<opis>.py` z identyfikatorem rewizji `<gałąź>_<NNNN>`; `alembic/env.py` i `alembic.ini` są wspólne. Zasada: `baza-wiedzy migrate` aktualizuje **wszystkie** gałęzie (rdzeń i każdy moduł w repozytorium), niezależnie od `ENABLED_MODULES`, więc schemat jest zawsze kompletny: późniejsze włączenie modułu nie wymaga migracji, a tabele modułu zawsze istnieją dla kluczy obcych innych modułów. `/ready` porównuje bazę ze wszystkimi głowami skryptów. Kosztem jest kilka pustych tabel wyłączonych modułów, co jest pomijalne. Nową rewizję tworzy `make revision branch=<gałąź> m="tekst"`. Obecne rewizje: `core_0001` (rozszerzenia), `core_0002` (tabele tożsamości i role systemowe), `core_0003` (dziennik audytu), `documents_0001` (domyślne nadania `documents.*`, `depends_on` `core_0002`), `documents_0002` (`doc_documents`, `doc_versions`, `doc_email_metadata`), `documents_0003` (`doc_documents.title_source`), `documents_0004` (kolumny cyklu życia), `ingestion_0001` (`ing_parts`, `ing_results`, `ing_outbox`, `depends_on` `documents_0002`), `search_0002` (`srch_parts`, `srch_documents`, `depends_on` `ingestion_0001` i `documents_0004`), `search_0001` (słownik i konfiguracje wyszukiwania tekstowego, `depends_on` `core_0001`). `alembic_version` przechowuje tylko głowy; rewizje wynikające z `depends_on` nie są wymienione. Modele SQLAlchemy (`core/db/models.py`) muszą być zgodne z migracjami; `make test-db` je porównuje.

## Tabele

### Tożsamość i uprawnienia

| Tabela | Kluczowe kolumny | Uwagi |
|---|---|---|
| `users` | `id` (uuid), `username`, `display_name`, `email`, `source` (`local`/`ad`), `password_hash` (tylko lokalni), `ad_object_guid`, `ad_dn`, `is_active`, `created_at`, `updated_at`, `last_login_at`, `password_changed_at` | `username` jest unikalna bez względu na wielkość liter (`lower(username)`), łącznie dla kont lokalnych i AD. Ograniczenie wymusza: lokalne ⇔ jest hash hasła. Użytkownicy lokalni istnieją tylko dla administratora awaryjnego (CLI). Użytkownicy AD powstają przy pierwszym logowaniu albo wcześniej, utworzeni przez admina (bez `ad_object_guid`, dopóki pierwsze logowanie ich nie połączy) |
| `roles` | `id`, `name` (unikalna bez względu na wielkość liter), `description`, `system_key` (`admin`/`publisher`/`reader` albo NULL), `created_at`, `updated_at` | Trzy role systemowe tworzy `core_0002`; kod rozpoznaje je po `system_key`, więc zmiana nazwy niczego nie psuje |
| `role_permissions` | `role_id`, `permission_code` | Kody uprawnień pochodzą z katalogu w kodzie ([auth-and-permissions.md](auth-and-permissions.md)). **Admin nie ma tu wierszy**: zawsze ma pełny katalog, liczony w kodzie. Domyślne nadania uprawnień modułu pochodzą z migracji modułu (`documents_0001`: Reader `documents.read`; Publisher `documents.read`, `documents.upload`, `documents.publish`) |
| `user_roles` | `user_id`, `role_id`, `source` (`manual`/`ad`), `created_at` | Klucz główny `(user_id, role_id, source)`. Uprawnienia efektywne = suma wszystkich ról. Wiersze z `source = ad` są przeliczane przy każdym logowaniu przez AD |
| `ad_group_role_map` | `id`, `ad_group_dn`, `role_id`, `created_at`, `created_by` | Unikalne `(lower(ad_group_dn), role_id)`. Edytowane przez admina w interfejsie |
| `sessions` | `id_hash` (SHA-256 tokenu z ciasteczka), `user_id`, `created_at`, `last_seen_at`, `expires_at` (koniec bezwzględny), `ip`, `user_agent` | Po stronie serwera, można unieważnić (unieważnienie usuwa wiersz). Sam token nigdy nie jest zapisywany |

### Dokumenty

| Tabela | Kluczowe kolumny | Uwagi |
|---|---|---|
| `doc_categories` | `id`, `name`, `parent_id` | Opcjonalne drzewo |
| `doc_documents` | `id`, `title`, `title_source` (`user`/`filename`/`subject`, `documents_0003`), `description`, `owner_id` → `users`, `status` (`draft`/`published`/`archived`), `latest_version_id`, `published_version_id` (wersja widoczna dla czytelników; ustawiana przy publikacji, zachowywana przy archiwizacji), `published_at`, `published_by` → `users`, `archived_at` (`documents_0004`), `created_at`, `updated_at` | Tworzone przez `documents_0002` (każde wgranie to `draft` z wersją 1); cykl życia w Fazie 3 (ADR-025). Warunek `ck_doc_documents_published_version`: opublikowany dokument wskazuje opublikowaną wersję. Planowane na Fazę 5: `category_id`. Widoczność w SQL: `documents.services.api.visibility_clause` |
| `doc_versions` | `id`, `document_id`, `version_no`, `kind` (`pdf`/`docx`/`doc`/`txt`/`eml`/`msg`), `original_filename` (po sanityzacji, tylko metadana), `size_bytes`, `sha256`, `storage_key` (unikalny, losowy UUID), `uploaded_by` → `users`, `uploaded_at`, `processing_status` (`pending`/`processing`/`done`/`failed`/`needs_ocr`), `processing_error` (komunikat dla użytkownika), `processing_attempts`, `processing_started_at`, `processed_at`, `page_count`, `ocr_applied` | Unikalne `(document_id, version_no)`; indeksy na `sha256` (ostrzeżenie o duplikacie) i `processing_status`. Planowane: `derived_key` (Faza 4), `change_note` (Faza 5) |
| `doc_email_metadata` | `version_id`, `subject`, `from_addr`, `to_addrs`, `cc_addrs`, `sent_at`, `message_id` | Tylko dla EML/MSG (wiadomość główna); wypełniane przez `ingestion` przez `documents.services.api`, uszkodzone wartości zostają puste |

### Ingestion

| Tabela | Kluczowe kolumny | Uwagi |
|---|---|---|
| `ing_parts` | `id` (bigint identity), `version_id` → `doc_versions` (`ON DELETE CASCADE`), `seq`, `part_type` (`page`/`body`/`attachment`), `origin` (ścieżka jsonb, np. `[{"type": "attachment", "name": "umowa.docx"}]`), `page_no`, `chunk_no`, `language` (`pl`/`en`/`unknown`), `text`, `char_count`, `needs_ocr`, `created_at` | Wyciągnięty tekst, najwyżej 100 000 znaków na wiersz (ADR-020). Unikalne `(version_id, seq)`. Zastępowane w całości w jednej transakcji przy ponownym przetworzeniu wersji |
| `ing_results` | `version_id` (PK, → `doc_versions`, kaskadowo), `notes` (lista jsonb: `encoding`, `skipped`, `failed`, `fallback` z pochodzeniem), `parts_count`, `chars_total`, `finished_at` | Adnotacje są widoczne na stronie dokumentu |
| `ing_outbox` | `id` (bigint identity), `event`, `payload` (jsonb, tylko identyfikatory), `created_at`, `delivered_at`, `attempts`, `last_error` (klasa wyjątku) | Indeks częściowy na niedostarczonych wierszach; dostarczone usuwane po `OUTBOX_RETENTION_DAYS` |

### Indeks wyszukiwania

| Tabela | Kluczowe kolumny | Uwagi |
|---|---|---|
| `srch_parts` | `id`, `version_id` → `doc_versions`, `document_id` → `doc_documents`, `seq`, `part_type` (`page`/`body`/`attachment`), `origin` (ścieżka JSONB: załączniki, strony), `page_no`, `chunk_no`, `language` (`pl`/`en`/`unknown`), `text_content`, `ident_terms` (znormalizowane identyfikatory), `tsv_pl`, `tsv_en`, `tsv_simple` (generowane z `text_content` i `ident_terms`) | `search_0002` (Faza 3). Jeden wiersz na część z tekstem (części bez tekstu, np. skany przed OCR, są pomijane); kopiowane przez `search` z `ing_parts` przez `ingestion.services.api` po `document.version.extracted` (ADR-020, ADR-026); unikalne `(version_id, seq)`; wiersze każdej wyodrębnionej wersji, przeszukiwaną wybiera `documents.services.api.search_scope` |
| `srch_documents` | `version_id` (PK) → `doc_versions`, `document_id` → `doc_documents`, `meta_a` (tytuł, temat maila), `meta_b` (słowa nazwy pliku, nadawca, odbiorcy), `ident_terms`, `meta_fold` (małe litery, bez znaków), `tsv_meta_pl`, `tsv_meta_en`, `tsv_meta_simple` (wagi A i B, generowane), `indexed_at` | `search_0002`. Metadane dopasowywane osobno, z wagami i trigramami; kopiowane przez `documents.services.api.index_metadata` |

### System

| Tabela | Kluczowe kolumny | Uwagi |
|---|---|---|
| `audit_log` | `id` (bigint identity), `at`, `actor_user_id`, `actor_label` (nazwa użytkownika w chwili zdarzenia albo `cli`), `action`, `entity_type`, `entity_id`, `ip`, `details` (jsonb) | Tylko dopisywanie: wyzwalacz odrzuca `UPDATE`, `DELETE` i `TRUNCATE` (`core_0003`). Bez kluczy obcych, więc wpisy przeżywają opisywane wiersze. Nazwy akcji: [auth-and-permissions.md](auth-and-permissions.md#log-audytowy). Znane ograniczenie: rola aplikacji jest właścicielem tabeli (wykonuje migracje), więc ktoś używający tej roli bazy bezpośrednio mógłby wyłączyć wyzwalacz; ochrona działa wobec aplikacji, nie wobec administratora bazy. Rozdzielenie ról bazy zaplanowano na Fazę 7 |
| `app_settings` | `key`, `value` (jsonb), `updated_by`, `updated_at` | Planowana, jeszcze nie istnieje: ustawienia niesekretne edytowane w panelu admina |

## Relacje

```
users ─< user_roles >─ roles ─< role_permissions
ad_group_role_map >─ roles
users ─< sessions
doc_documents ─< doc_versions ─< ing_parts, ing_results (kaskadowo)
doc_versions ─< srch_parts
doc_versions ─  srch_documents (1:1)
doc_versions ─ 0..1 ─ doc_email_metadata
doc_documents >─ doc_categories
```

## Indeksy

| Indeks | Cel |
|---|---|
| GIN na `srch_parts.tsv_pl`, `tsv_en`, `tsv_simple` | Wyszukiwanie pełnotekstowe |
| btree `srch_parts(version_id, seq)` (unikalny), `srch_parts(document_id)`, `srch_documents(document_id)` | Złączenia |
| GIN na `srch_documents.tsv_meta_pl`, `tsv_meta_en`, `tsv_meta_simple`; GIN `gin_trgm_ops` na `srch_documents.meta_fold` | Wyszukiwanie po metadanych, dopasowania przybliżone |
| GIN (`gin_trgm_ops`) na `doc_documents.title`, `doc_versions.original_filename`, `doc_email_metadata.subject`, `doc_email_metadata.from_addr` | Wyszukiwanie przybliżone po metadanych |
| btree `doc_versions(sha256)` | Ostrzeżenie o duplikacie |
| btree `doc_documents(status, category_id)` | Listy i filtry |
| btree `audit_log(at)`, `(actor_user_id)`, `(action)`, `(entity_type, entity_id)` | Przeglądarka audytu |
| unikalne `users(lower(username))`, `users(ad_object_guid)`, `roles(lower(name))`, `roles(system_key)` | Nazwy bez względu na wielkość liter, tożsamość AD |
| btree `sessions(user_id)`, `sessions(expires_at)`, `user_roles(role_id)` | Unieważnianie, sprzątanie wygasłych sesji, użycie ról |

## Obiekty wyszukiwania tekstowego

Tworzone wyłącznie przez migracje Alembic, w schemacie `public`, jako zwykły użytkownik aplikacji (obraz `db` zawiera tylko pliki słownika):
- rozszerzenia `pg_trgm`, `unaccent` (`core_0001`)
- słownik `pl_hunspell` (`search_0001`), konfiguracja `pl` (Hunspell, potem `simple` dla słów nieznanych)
- konfiguracja `simple_unaccent`
- wbudowana `english`

Zobacz [search.md](search.md).

## Niezmienniki

- Co najmniej jeden aktywny użytkownik ma rolę Admin.
- `doc_documents.published_version_id` zawsze wskazuje wersję tego samego dokumentu; jej status przetwarzania może być dowolny (publikacja nie zależy od przetwarzania, ADR-025).
- Dokument o statusie `published` ma niepuste `published_version_id`.
- Pliki oryginalne nigdy nie są modyfikowane; `sha256` zapisanego pliku zgadza się z kolumną.
- Aplikacja nigdy nie aktualizuje ani nie usuwa wierszy `audit_log`.

## Usuwanie i RODO

- Archiwizacja = ukrycie w wyszukiwarce, plik zostaje na dysku.
- Trwałe usunięcie (uprawnienie `documents.delete`) kasuje wiersze w bazie, plik oryginalny i pliki pochodne oraz zapisuje wpis audytowy, który nie zawiera treści dokumentu.
- Okresy retencji to otwarte pytanie, zobacz [decisions.md](decisions.md).
