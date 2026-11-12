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
| `proxy` | `baza-wiedzy-proxy` (Caddy, budowany lokalnie z `docker/caddy`) | Terminacja TLS, reverse proxy, limit rozmiaru żądania; działa bez roota na portach 8080/8443 wewnątrz kontenera | 80, 443 |
| `app` | `baza-wiedzy-app` | Interfejs WWW, API, uprawnienia, zapytania wyszukiwania | wewnętrzny |
| `worker` | `baza-wiedzy-worker` | Wyciąganie tekstu, OCR, indeksacja (w środku Tesseract i LibreOffice) | brak |
| `db` | `baza-wiedzy-db` | PostgreSQL 16 + słownik Hunspell dla polskiego | brak |
| `redis` | `redis` 8.2 | Tylko kolejka zadań (bez danych biznesowych); włączony plik append-only, więc zadania w kolejce przetrwają restart; wymagane hasło | brak |
| `clamav` | `clamav` | Opcjonalne skanowanie antywirusowe, profil compose `antivirus`, domyślnie wyłączone | brak |

Licencja Redisa: Redis 8 jest dostępny m.in. na AGPLv3, co jest akceptowalne przy użyciu wewnętrznym bez modyfikacji. Gdyby to kiedyś budziło wątpliwości, zamiennikiem 1:1 jest [Valkey](https://valkey.io) (BSD-3-Clause): zmień obraz `redis` na `valkey/valkey` z przypiętym tagiem; nic więcej się nie zmienia.

`app` i `worker` mają wspólny kod Pythona i modele bazy; różnią się zawartością obrazu (obraz workera ma OCR i LibreOffice) oraz poleceniem startowym.

## Struktura modularna

Aplikacja to modularny monolit: jedna baza kodu i jeden obraz na rolę (`app`, `worker`), złożone z zawsze włączonego **rdzenia** i opcjonalnych **modułów**. Moduły wybiera się zmienną `ENABLED_MODULES` w `.env`; `app` i `worker` muszą mieć tę samą wartość.

| Część | Wymaga | Jest właścicielem tabel | Publikuje zdarzenia | Subskrybuje |
|---|---|---|---|---|
| rdzeń | nic | `users`, `roles`, `sessions`, `audit_log`, `app_settings` | nic | nic |
| `documents` | nic | `doc_*` | `document.version.uploaded`, `document.published` | nic |
| `ingestion` | `documents` | brak w MVP (prefiks `ing_*` zarezerwowany) | `document.version.extracted` | `document.version.uploaded` |
| `search` | `documents` | `srch_*` | nic | `document.version.extracted` |

Zasady (egzekwowane w CI przez import-linter, zobacz ADR-014):
- Moduł nigdy nie importuje wnętrza innego modułu. Może importować `baza_wiedzy.core` oraz `services/api.py` modułu, którego wymaga. Wszystko inne idzie przez zdarzenia.
- Zdarzenia są synchroniczne i działają w obrębie procesu: handler wykonuje się w procesie, który publikuje zdarzenie. Dlatego `worker` ładuje także `search` i indeksuje wyciągnięty tekst wewnątrz workera; w `app` handler `ingestion` tylko kolejkuje zadanie.
- Przy starcie rejestr sprawdza `requires` i zatrzymuje się z czytelnym komunikatem, gdy wymagany moduł nie jest włączony.
- Uprawnienia, pozycje menu, routery, zadania w tle, polecenia CLI i gałęzie migracji są zbierane z manifestów modułów (`module.py`).
- Nie ma wtyczek spoza repozytorium.

Dokumentacja poszczególnych modułów: [documents](modules/documents.md), [ingestion](modules/ingestion.md), [search](modules/search.md).

## Stack technologiczny

Obowiązująca tabela jest w `CLAUDE.md`, sekcja 3. W skrócie: Python 3.12, FastAPI, Jinja2 + HTMX, SQLAlchemy 2 (sync) + psycopg 3, Alembic, wyszukiwanie pełnotekstowe PostgreSQL z `pg_trgm` i `unaccent`, Dramatiq + Redis, pypdfium2, OCRmyPDF + Tesseract, ldap3, Caddy.

## Główne przepływy

### Upload
1. Użytkownik z uprawnieniem `documents.upload` wysyła plik.
2. `app` waliduje go (rozmiar, typ według zawartości), zapisuje pod kluczem UUID, tworzy wiersze `doc_documents` i `doc_versions` ze statusem `processing_status = pending`, publikuje zdarzenie `document.version.uploaded` (moduł `ingestion` kolejkuje `process_version(version_id)`) i zapisuje wpis audytowy.
3. Przeglądarka odpytuje o status wersji (HTMX) i pokazuje postęp.
4. `worker` wyciąga tekst, w razie potrzeby uruchamia OCR, wykrywa język, publikuje `document.version.extracted` z częściami tekstu (moduł `search` zapisuje je i indeksuje w `srch_parts`) i ustawia status `done` lub `failed` przez `documents.services.api`. Szczegóły: [ingestion-and-ocr.md](ingestion-and-ocr.md).

### Publikacja
Użytkownik z `documents.publish` (własne dokumenty) lub `documents.publish_any` publikuje wersję: ustawiane jest `doc_documents.published_version_id`, a status zmienia się na `published`. Dopiero wtedy czytelnicy widzą dokument i mogą go znaleźć.

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
- Moduły rozmawiają ze sobą wyłącznie przez `services/api.py` i zdarzenia.
- Konfiguracja wyłącznie przez zmienne środowiskowe.
- Teksty interfejsu: identyfikatory komunikatów są po angielsku i opakowane w `_()` w szablonach i kodzie; katalog polski to `src/baza_wiedzy/core/web/locale/pl/LC_MESSAGES/messages.po`. Jest kompilowany w pamięci przy starcie (bez plików `.mo` w repozytorium i obrazie). Po zmianie tekstów uruchom `make messages` i przetłumacz nowe pozycje `msgstr`; test jednostkowy nie przejdzie, dopóki jakikolwiek tekst jest nieprzetłumaczony. `APP_NAME` pochodzi z konfiguracji i nigdy nie jest wpisany na sztywno.
- Pliki statyczne (CSS, HTMX) serwuje `app` z `src/baza_wiedzy/core/web/static/`; nic nie jest ładowane z CDN. HTMX 2.0.11 jest dołączony w `static/vendor/` razem z licencją (0BSD). Szablony linkują tylko do ścieżek, nigdy do adresów bezwzględnych.
- Logi: JSON na stdout, zbierane przez Dockera z rotacją.
- Zdrowie: `/health` (proces żyje), `/ready` (baza, Redis, heartbeat workera, wersja schematu).
