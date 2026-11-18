# Architektura

> Dotyczy: v0.3.0 · Ostatnia aktualizacja: 2026-11-17

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
| `proxy` | `baza-wiedzy-proxy` (Caddy, z `docker/caddy`) | Terminacja TLS, reverse proxy, limit rozmiaru żądania; działa bez roota na portach 8080/8443 wewnątrz kontenera | 80, 443 |
| `app` | `baza-wiedzy-app` | Interfejs WWW, API, uprawnienia, zapytania wyszukiwania | wewnętrzny |
| `worker` | `baza-wiedzy-worker` | Wyciąganie tekstu, OCR, indeksacja (w środku Tesseract i LibreOffice) | brak |
| `db` | `baza-wiedzy-db` | PostgreSQL 16 + słownik Hunspell dla polskiego | brak |
| `redis` | `redis` 8.2 | Tylko kolejka zadań (bez danych biznesowych); włączony plik append-only, więc zadania w kolejce przetrwają restart; wymagane hasło | brak |
| `clamav` | `clamav` | Opcjonalne skanowanie antywirusowe, profil compose `antivirus`, domyślnie wyłączone | brak |

Sieci (ADR-024):

| Sieć | Członkowie | Internet |
|---|---|---|
| `default` (`DOCKER_SUBNET`, domyślnie `172.30.70.0/24`; proxy pod `PROXY_IP`) | `proxy`, `app` (`clamav` z profilem `antivirus`, do aktualizacji sygnatur) | tak: ruch przychodzący tylko przez porty wystawione przez proxy; wychodzący dla `app` (Active Directory przez LDAPS) |
| `backend` (`internal: true`, `BACKEND_SUBNET`, domyślnie `172.30.72.0/24`) | `app`, `worker`, `db`, `redis` (`clamav`) | brak: Docker nie tworzy bramy; publiczne nazwy nawet się nie rozwiązują |

Worker, który parsuje niezaufane pliki kodem natywnym, sięga tylko do bazy, Redisa i aplikacji; proxy jest jedynym punktem wejścia. Sprawdza to `make smoke`: z workera odpowiadają `db` i `redis`, a `1.1.1.1:443`, `8.8.8.8:53` i nazwa `example.com` nie.

Licencja Redisa: Redis 8 jest dostępny m.in. na AGPLv3, co jest akceptowalne przy użyciu wewnętrznym bez modyfikacji. Gdyby to kiedyś budziło wątpliwości, zamiennikiem 1:1 jest [Valkey](https://valkey.io) (BSD-3-Clause): zmień obraz `redis` na `valkey/valkey` z przypiętym tagiem; nic więcej się nie zmienia.

`app` i `worker` mają wspólny kod Pythona i modele bazy; różnią się zawartością obrazu (obraz workera ma OCR i LibreOffice) oraz poleceniem startowym.

## Struktura modularna

Aplikacja to modularny monolit: jedna baza kodu i jeden obraz na rolę (`app`, `worker`), złożone z zawsze włączonego **rdzenia** i opcjonalnych **modułów**. Moduły wybiera się zmienną `ENABLED_MODULES` w `.env`; `app` i `worker` muszą mieć tę samą wartość.

| Część | Wymaga | Jest właścicielem tabel | Publikuje zdarzenia | Subskrybuje |
|---|---|---|---|---|
| rdzeń | nic | `users`, `roles`, `sessions`, `audit_log`, `app_settings` | nic | nic |
| `documents` | nic | `doc_*` | `document.version.uploaded`, `document.published` | nic |
| `ingestion` | `documents` | `ing_*` (wyciągnięty tekst, adnotacje, outbox) | `document.version.extracted` (outbox, tylko identyfikatory) | `document.version.uploaded` |
| `search` | `documents` (Faza 3: także `ingestion`, ADR-020) | `srch_*` | nic | `document.version.extracted` |

Zasady (egzekwowane w CI przez import-linter, zobacz ADR-014):
- Moduł nigdy nie importuje wnętrza innego modułu. Może importować `baza_wiedzy.core` oraz `services/api.py` modułu, którego wymaga. Wszystko inne idzie przez zdarzenia.
- Zdarzenia są synchroniczne i działają w obrębie procesu: handler wykonuje się w procesie, który publikuje zdarzenie. Dlatego `worker` ładuje także `search` i indeksuje wyciągnięty tekst wewnątrz workera; w `app` handler `ingestion` tylko kolejkuje zadanie. Magistrala niczego nie utrwala: `document.version.extracted` idzie przez outbox modułu ingestion (`ing_outbox`, zapisywany w tej samej transakcji co części, dostarczany co najmniej raz), a ładunki niosą tylko identyfikatory (ADR-020).
- Przy starcie rejestr sprawdza `requires` i zatrzymuje się z czytelnym komunikatem, gdy wymagany moduł nie jest włączony.
- Uprawnienia, pozycje menu, routery, zadania w tle, polecenia CLI i gałęzie migracji są zbierane z manifestów modułów (`module.py`). `MenuItem` ma `section` (`main` albo `admin`, nawigacja panelu admina), uprawnienie, które go pokazuje, oraz `available` (false, dopóki jego strony nie istnieją); etykiety i opisy uprawnień są oznaczone `N_()`, żeby `make messages` wyciągnęło je do tłumaczenia.
- Nie ma wtyczek spoza repozytorium.

Dokumentacja poszczególnych modułów: [documents](modules/documents.md), [ingestion](modules/ingestion.md), [search](modules/search.md).

## Stack technologiczny

Obowiązująca tabela jest w `CLAUDE.md`, sekcja 3. W skrócie: Python 3.12, FastAPI, Jinja2 + HTMX, SQLAlchemy 2 (sync) + psycopg 3, Alembic, wyszukiwanie pełnotekstowe PostgreSQL z `pg_trgm` i `unaccent`, Dramatiq + Redis, pypdfium2, OCRmyPDF + Tesseract, ldap3, Caddy.

## Główne przepływy

### Upload
1. Użytkownik z uprawnieniem `documents.upload` wysyła plik.
2. `app` waliduje go (rozmiar, typ według zawartości), zapisuje pod kluczem UUID, tworzy wiersze `doc_documents` i `doc_versions` ze statusem `processing_status = pending`, publikuje zdarzenie `document.version.uploaded` (moduł `ingestion` kolejkuje `process_version(version_id)`) i zapisuje wpis audytowy.
3. Przeglądarka odpytuje o status wersji (HTMX) i pokazuje postęp.
4. `worker` wyciąga tekst, w razie potrzeby uruchamia OCR (Faza 4), wykrywa język, zapisuje części w `ing_parts`, ustawia status `done` lub `failed` przez `documents.services.api` i zapisuje `document.version.extracted` w outboxie, wszystko w jednej transakcji; potem dostarcza zdarzenie (od Fazy 3 moduł `search` kopiuje części do `srch_parts` przez `ingestion.services.api`). Błędy przejściowe są ponawiane z opóźnieniem; start workera odzyskuje zawieszone i oczekujące wersje. Szczegóły: [ingestion-and-ocr.md](ingestion-and-ocr.md).

### Publikacja
Użytkownik z `documents.publish` (własne dokumenty) lub `documents.publish_any` publikuje wersję: ustawiane jest `doc_documents.published_version_id`, a status zmienia się na `published`. Dopiero wtedy czytelnicy widzą dokument i mogą go znaleźć.

### Wyszukiwanie
Użytkownik wpisuje frazę; `app` buduje zapytania dla każdej konfiguracji wyszukiwania, stosuje reguły widoczności wewnątrz SQL, szereguje wyniki, a fragmenty (snippety) dodaje tylko dla widocznej strony wyników. Szczegóły: [search.md](search.md).

### Logowanie
Active Directory przez LDAPS (konto serwisowe znajduje użytkownika, potem bind z jego własnymi poświadczeniami weryfikuje hasło), grupy mapują się na role. Na wypadek awarii istnieje lokalny administrator awaryjny. Szczegóły: [auth-and-permissions.md](auth-and-permissions.md).

## Przechowywanie plików

```
/data/storage/originals/<aa>/<bb>/<klucz>  plik oryginalny (niezmienny, tylko do odczytu, klucz = losowy UUID)
/data/storage/.incoming/<uuid>.part        wgrywanie w toku (ten sam wolumen, potem atomowa zmiana nazwy)
/data/storage/derived/<klucz>.pdf          PDF po OCR (z warstwą tekstu), podglądy (Faza 4)
/data/tmp/                                 katalog roboczy workera (czyszczony po każdym zadaniu)
```

`<aa>/<bb>` to pierwsze znaki klucza, żeby nie tworzyć ogromnych katalogów. Nazwa pliku użytkownika nigdy nie jest częścią ścieżki. `.incoming/` leży na tym samym wolumenie co `originals/`, więc ostatnie przeniesienie to atomowa zmiana nazwy; pliki starsze niż godzina to pozostałości przerwanych wgrań i są usuwane (przy każdym wgraniu i poleceniem `baza-wiedzy storage-cleanup`). Magazyn plików należy do modułu `documents` (`modules/documents/storage.py`).

## Zasady przekrojowe

- Widoczność jest wymuszana w SQL. Sprawdzanie uprawnień jest w serwisach i zależnościach.
- Kod wyszukiwania jest izolowany za interfejsem `SearchBackend`.
- Moduły rozmawiają ze sobą wyłącznie przez `services/api.py` i zdarzenia.
- Konfiguracja wyłącznie przez zmienne środowiskowe.
- Teksty interfejsu: identyfikatory komunikatów są po angielsku i opakowane w `_()` w szablonach i kodzie; katalog polski to `src/baza_wiedzy/core/web/locale/pl/LC_MESSAGES/messages.po`. Jest kompilowany w pamięci przy starcie (bez plików `.mo` w repozytorium i obrazie). Po zmianie tekstów uruchom `make messages` i przetłumacz nowe pozycje `msgstr`; test jednostkowy nie przejdzie, dopóki jakikolwiek tekst jest nieprzetłumaczony. `APP_NAME` pochodzi z konfiguracji i nigdy nie jest wpisany na sztywno.
- Pliki statyczne (CSS, HTMX) serwuje `app` z `src/baza_wiedzy/core/web/static/`; nic nie jest ładowane z CDN. HTMX 2.0.11 jest dołączony w `static/vendor/` razem z licencją (0BSD). Szablony linkują tylko do ścieżek, nigdy do adresów bezwzględnych.
- Zadania w tle: Dramatiq na Redisie. Actory są deklarowane w module i wymienione w jego manifeście (`tasks`); przy starcie `baza_wiedzy.worker` wiąże actory włączonych modułów z brokerem. Każdy proces workera co 15 s zapisuje w Redisie klucz heartbeatu (wygasa po 60 s); sprawdza go `/ready`. Zadanie testowe: `core.ping` (`baza-wiedzy worker-ping`).
- Logi: linie JSON (`baza_wiedzy.core.logs`) na stdout (`app`) i stderr (procesy workera), zbierane przez Dockera z rotacją; poziom z `LOG_LEVEL`.
- Zdrowie: `/health` (proces żyje), `/ready` (baza, Redis, heartbeat workera, wersja schematu).
