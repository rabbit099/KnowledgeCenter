# Architektura

> Dotyczy: v0.0.0 (przed pierwszym wydaniem) · Ostatnia aktualizacja: 2026-11-12

## Przegląd

Aplikacja renderowana po stronie serwera. Przeglądarka rozmawia wyłącznie z reverse proxy. Ciężka praca (wyciąganie tekstu, OCR) odbywa się w osobnym workerze, dzięki czemu upload wraca natychmiast.

```
Przeglądarka ──HTTPS──► Caddy ──► app (FastAPI + Jinja2 + HTMX)
                                   │        │
                                   │        └──► Redis (kolejka zadań) ──► worker (Dramatiq)
                                   │                                         │  pypdfium2, OCRmyPDF/Tesseract,
                                   ▼                                         │  LibreOffice, extract-msg
                               PostgreSQL ◄──────────────────────────────────┘
                          (dane + indeks pełnotekstowy)

                 Wspólny wolumen: /data/storage (pliki oryginalne i pochodne)
                 App ──LDAPS──► Active Directory
```

## Usługi (Docker Compose)

| Usługa | Obraz | Rola | Port wystawiony |
|---|---|---|---|
| `proxy` | Caddy | Terminacja TLS, reverse proxy, limit rozmiaru żądania | 80, 443 |
| `app` | `baza-wiedzy-app` | Interfejs WWW, API, uprawnienia, zapytania wyszukiwania | wewnętrzny |
| `worker` | `baza-wiedzy-worker` | Wyciąganie tekstu, OCR, indeksacja (w środku Tesseract i LibreOffice) | brak |
| `db` | `baza-wiedzy-db` | PostgreSQL 16 + słownik Hunspell dla polskiego | brak |
| `redis` | `redis` | Tylko kolejka zadań (bez trwałych danych biznesowych) | brak |
| `clamav` | `clamav` | Opcjonalne skanowanie antywirusowe, profil compose `antivirus`, domyślnie wyłączone | brak |

`app` i `worker` mają wspólny kod Pythona i modele bazy; różnią się zawartością obrazu (obraz workera ma OCR i LibreOffice) oraz poleceniem startowym.

## Stack technologiczny

Obowiązująca tabela jest w `CLAUDE.md`, sekcja 3. W skrócie: Python 3.12, FastAPI, Jinja2 + HTMX, SQLAlchemy 2 (sync) + psycopg 3, Alembic, wyszukiwanie pełnotekstowe PostgreSQL z `pg_trgm` i `unaccent`, Dramatiq + Redis, pypdfium2, OCRmyPDF + Tesseract, ldap3, Caddy.

## Główne przepływy

### Upload
1. Użytkownik z uprawnieniem `documents.upload` wysyła plik.
2. `app` waliduje go (rozmiar, typ według zawartości), zapisuje pod kluczem UUID, tworzy wiersze `documents` i `document_versions` ze statusem `processing_status = pending`, kolejkuje `process_version(version_id)` i zapisuje wpis audytowy.
3. Przeglądarka odpytuje o status wersji (HTMX) i pokazuje postęp.
4. `worker` wyciąga tekst, w razie potrzeby uruchamia OCR, wykrywa język, zapisuje `document_parts` i ustawia status `done` lub `failed`. Szczegóły: [ingestion-and-ocr.md](ingestion-and-ocr.md).

### Publikacja
Użytkownik z `documents.publish` (własne dokumenty) lub `documents.publish_any` publikuje wersję: ustawiane jest `documents.published_version_id`, a status zmienia się na `published`. Dopiero wtedy czytelnicy widzą dokument i mogą go znaleźć.

### Wyszukiwanie
Użytkownik wpisuje frazę; `app` buduje zapytania dla każdej konfiguracji wyszukiwania, stosuje reguły widoczności wewnątrz SQL, szereguje wyniki, a fragmenty (snippety) dodaje tylko dla widocznej strony wyników. Szczegóły: [search.md](search.md).

### Logowanie
Active Directory przez LDAPS (konto serwisowe znajduje użytkownika, potem bind z jego własnymi poświadczeniami weryfikuje hasło), grupy mapują się na role. Na wypadek awarii istnieje lokalny administrator awaryjny. Szczegóły: [auth-and-permissions.md](auth-and-permissions.md).

## Przechowywanie plików

```
/data/storage/<aa>/<bb>/<uuid>          plik oryginalny (niezmienny)
/data/storage/derived/<uuid>.pdf        PDF po OCR (z warstwą tekstu), podglądy
/data/tmp/                              katalog roboczy workera (czyszczony po każdym zadaniu)
```

`<aa>/<bb>` to pierwsze znaki UUID, żeby nie tworzyć ogromnych katalogów.

## Zasady przekrojowe

- Widoczność jest wymuszana w SQL. Sprawdzanie uprawnień jest w serwisach i zależnościach.
- Kod wyszukiwania jest izolowany za interfejsem `SearchBackend`.
- Konfiguracja wyłącznie przez zmienne środowiskowe.
- Logi: JSON na stdout, zbierane przez Dockera z rotacją.
- Zdrowie: `/health` (proces żyje), `/ready` (baza, Redis, heartbeat workera, wersja schematu).
