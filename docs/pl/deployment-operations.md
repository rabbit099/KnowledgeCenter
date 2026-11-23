# Wdrożenie i utrzymanie

> Dotyczy: v0.4.1 · Ostatnia aktualizacja: 2026-11-20

Wszystko, czego potrzebuje operator: instalacja, konfiguracja, aktualizacje, rollback, backup, odtwarzanie, monitoring.

## Wymagania

- Serwer z Linuksem, Docker Engine i wtyczka Docker Compose.
- Dostęp do `ghcr.io` (albo procedura offline opisana niżej).
- Nazwa DNS aplikacji i certyfikat TLS (wewnętrzne CA lub Let's Encrypt — do ustalenia).
- Osiągalność kontrolera domeny przez LDAPS (port 636) i konto serwisowe do odczytu LDAP.
- Miejsce na dysku na dokumenty (oryginały + kopie pochodne + baza). Na start zaplanuj 3× rozmiar wgrywanych plików.

## Układ na serwerze

```
/srv/baza-wiedzy/
├── docker-compose.yml
├── .env                 # konfiguracja i sekrety (chmod 600)
├── scripts/             # update.sh, rollback.sh, backup.sh, restore.sh
├── secrets/tls/         # cert.pem i key.pem, tylko przy TLS_MODE=files
├── secrets/ldap/        # ca.pem i bind_password dla Active Directory (właściciel 10001, tryb 400)
├── logs/                # update.log, backup.log
└── backups/             # tworzone przez backup.sh
```

Dane są w nazwanych wolumenach Dockera projektu Compose `baza-wiedzy`: `pgdata` (baza), `storage` (pliki wgrane i pochodne, zamontowany w `/data/storage` w `app` i `worker`), `redis-data` (kolejka), `caddy-data` i `caddy-config` (certyfikaty, w tym wewnętrzne CA Caddy), `clamav-db` (tylko z profilem `antivirus`). Lista: `docker volume ls --filter name=baza-wiedzy`. `docker compose down` je zachowuje; `docker compose down -v` **usuwa wszystkie dane**.

## Konfiguracja

Wszystkie ustawienia to zmienne środowiskowe w `.env`; `.env.example` w repozytorium opisuje każdą zmienną i jest źródłem prawdy. Kluczowe zmienne:

| Zmienna | Znaczenie |
|---|---|
| `APP_NAME` | Wyświetlana nazwa (domyślnie „Baza Wiedzy”) |
| `APP_VERSION` | Tag obrazów projektu do uruchomienia, na przykład `0.1.0` (wymagany przez `docker-compose.yml`). Wybiera wyłącznie obrazy: aplikacja podaje własną wersję z zainstalowanego pakietu (`version` w `pyproject.toml`), widoczną w `/health`, `/ready` i w stopce strony |
| `LOG_LEVEL` | `DEBUG`, `INFO` (domyślnie), `WARNING`, `ERROR`; logi to linie JSON (zob. Monitoring) |
| `ENABLED_MODULES` | Opcjonalne moduły do uruchomienia, oddzielone przecinkami (domyślnie `documents,ingestion,search`); rdzeń jest zawsze włączony |
| `PUBLIC_HOSTNAME` | Nazwa DNS używana przez Caddy |
| `TLS_MODE` | `internal`, `files` lub `acme` |
| `SECRET_KEY` | Podpisuje tokeny CSRF; co najmniej 32 znaki, inaczej aplikacja odmawia startu. Generowanie: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `SESSION_IDLE_MINUTES`, `SESSION_ABSOLUTE_HOURS` | Limit bezczynności sesji (120) i bezwzględny czas życia (12) |
| `LOGIN_WINDOW_MINUTES`, `LOGIN_LOCKOUT_MINUTES`, `LOGIN_MAX_FAILURES_ACCOUNT`, `LOGIN_MAX_FAILURES_ACCOUNT_IP`, `LOGIN_MAX_FAILURES_IP` | Limity nieudanych logowań ([auth-and-permissions.md](auth-and-permissions.md)) |
| `DOCKER_SUBNET`, `PROXY_IP` | Stała sieć Dockera stosu (domyślnie `172.30.70.0/24`) i adres proxy w niej (`172.30.70.10`); aplikacja ufa `X-Forwarded-For` tylko od `PROXY_IP` |
| `HTTP_PORT`, `HTTPS_PORT` | Porty hosta dla proxy (domyślnie 80, 443) |
| `IMAGE_PREFIX` | Rejestr i prefiks nazw obrazów, na przykład `ghcr.io/<właściciel>/baza-wiedzy` |
| `BACKEND_SUBNET` | Wewnętrzna sieć `app`, `worker`, `db`, `redis` bez dostępu do internetu (domyślnie `172.30.72.0/24`; zmień, jeśli koliduje z siecią firmy, ADR-024) |
| `ACME_EMAIL`, `MAX_REQUEST_MB` | Kontakt dla Let's Encrypt (`TLS_MODE=acme`); największe ciało żądania przyjmowane przez proxy (domyślnie 501, trzymaj `UPLOAD_MAX_TOTAL_MB` + 1; mniejsza wartość obniża łączny limit aplikacji do `MAX_REQUEST_MB` − 1, wartość nie większa od `MAX_UPLOAD_MB` zatrzymuje aplikację przy starcie) |
| `MAX_UPLOAD_MB`, `STORAGE_DIR` | Największy wgrywany plik (domyślnie 50 MB); ścieżka wolumenu plików w kontenerach (domyślnie `/data/storage`) |
| `UPLOAD_MAX_FILES`, `UPLOAD_MAX_TOTAL_MB`, `UPLOAD_BATCH_HOURS` | Pliki w jednym formularzu (50), ich łączny rozmiar (500 MB; kontener app potrzebuje tyle wolnego miejsca tymczasowego), godziny oczekiwania możliwego duplikatu na decyzję (24) |
| `INGEST_MAX_ATTEMPTS`, `INGEST_RETRY_MIN_SECONDS`, `INGEST_RETRY_MAX_SECONDS`, `INGEST_TIME_LIMIT_SECONDS`, `INGEST_STALE_MINUTES` | Przetwarzanie w workerze: liczba prób przy błędach przejściowych (3), opóźnienie między nimi (30 s, podwajane do 600 s), limit czasu jednej próby (900 s), wiek, po którym wersja wciąż „przetwarzana” uchodzi za przerwaną (30 min, trzymaj powyżej limitu czasu) |
| `INGEST_MAX_DEPTH`, `INGEST_MAX_PARTS`, `INGEST_MAX_TEXT_MB`, `LANGUAGE_MIN_LETTERS` | Limity ekstrakcji na plik: głębokość załączników maila (3), liczba części (2000), tekst (50 MB); liczba liter potrzebna do wykrycia języka (40) |
| `OUTBOX_RETENTION_DAYS`, `SCAN_HOOK` | Liczba dni, przez które dostarczone zdarzenie zostaje w `ing_outbox` (7); krok skanowania antywirusowego przed ekstrakcją (`none`, na razie jedyna wartość) |
| `POSTGRES_PASSWORD` | Hasło superużytkownika PostgreSQL; używane tylko wewnątrz kontenera `db` |
| `APP_DB_USER` / `APP_DB_PASSWORD` / `APP_DB_NAME` | Zwykły użytkownik (nie superużytkownik), właściciel bazy aplikacji; używają go aplikacja i migracje. `DATABASE_URL` jest z nich składany w `docker-compose.yml` |
| `POSTGRES_MAX_CONNECTIONS`, `POSTGRES_SHARED_BUFFERS` | Jawny limit połączeń i bufory współdzielone (zob. niżej) |
| `DB_MEMORY_LIMIT`, `APP_MEMORY_LIMIT`, `WORKER_MEMORY_LIMIT`, `REDIS_MEMORY_LIMIT`, `CLAMAV_MEMORY_LIMIT` | Limity pamięci kontenerów |
| `WORKER_PROCESSES` | Liczba procesów workera (domyślnie 2; każdy ma jeden wątek i jedno połączenie z bazą; nie więcej niż rdzeni CPU). Pamięć zmierzona przy 2 procesach: 173 MiB w spoczynku, poniżej 0,5 GB pod obciążeniem; trzymaj `WORKER_MEMORY_LIMIT` (domyślnie 2g) na co najmniej 1,4 GB + 0,6 GB na proces ([ingestion-and-ocr.md](ingestion-and-ocr.md#kod-natywny-w-procesach-potomnych)) |
| `OCR_MEMORY_LIMIT`, `OCR_CPUS` | Limity pamięci (domyślnie 3g) i CPU (domyślnie 2) usługi `ocr-worker` (jeden dokument OCR naraz, w środku `OCR_JOBS` procesów Tesseracta). Rdzenie serwera ≈ `WORKER_PROCESSES` + `OCR_CPUS` to dobry punkt wyjścia |
| `INGEST_MAX_PAGES`, `OCR_MIN_CHARS`, `INGEST_CHILD_MEMORY_MB`, `INGEST_CHILD_TIMEOUT_SECONDS`, `LIBREOFFICE_MEMORY_MB`, `LIBREOFFICE_TIMEOUT_SECONDS` | Limit stron PDF (2000) i próg stron „wymaga OCR” (25 znaków); limity pamięci i czasu procesu potomnego PDF (1024 MB, 300 s) i LibreOffice (1024 MB, 120 s) |
| `OCR_ENABLED`, `OCR_LANGUAGES`, `OCR_AUTO_MAX_PAGES`, `OCR_MAX_PAGES`, `OCR_TIMEOUT_SECONDS`, `OCR_PAGE_TIMEOUT_SECONDS`, `OCR_JOBS`, `OCR_MEMORY_MB`, `OCR_MAX_IMAGE_MPIXELS`, `OCR_ROTATE_THRESHOLD` | OCR zeskanowanych stron (true, `pol+eng`, automatycznie do 30 zeskanowanych stron, najwyżej 200, 1800 s na dokument, 120 s na stronę, 2 procesy Tesseracta, po 2048 MB, 200 megapikseli, pewność obrotu 5); zobacz ingestion-and-ocr.md |
| `REDIS_PASSWORD` | Hasło wymagane przez Redis (`requirepass`); tylko znaki bezpieczne w URL. `REDIS_URL` jest z niego składany w `docker-compose.yml` |
| `MAX_UPLOAD_MB` i inne limity | Zobacz [ingestion-and-ocr.md](ingestion-and-ocr.md) |
| `LDAP_*` | Logowanie przez Active Directory, domyślnie wyłączone (`LDAP_ENABLED=false`). `./secrets/ldap/` jest montowany tylko do odczytu w `/run/secrets/ldap/` w `app` dla certyfikatu CA i pliku z hasłem konta serwisowego. Szczegóły, normalizacja nazw i procedura testu na prawdziwym AD: [auth-and-permissions.md](auth-and-permissions.md#active-directory-ldap-przez-tls) |

`ENABLED_MODULES` musi mieć tę samą wartość dla `app` i `worker` (oba czytają ten sam `.env`). Przy starcie aplikacja sprawdza zależności modułów i odmawia uruchomienia z komunikatem w rodzaju `module 'search' requires 'documents', which is not enabled`. `baza-wiedzy migrate` stosuje migracje rdzenia i **wszystkich** modułów, niezależnie od `ENABLED_MODULES`: schemat jest zawsze kompletny, więc późniejsze włączenie modułu nie wymaga migracji, a `/ready` porównuje bazę ze wszystkimi głowami migracji. Wyłączenie modułu nie usuwa jego tabel ani danych.

Użytkownik bazy i uprawnienia: aplikacja i `baza-wiedzy migrate` łączą się jako jeden użytkownik bazy, który **nie** jest superużytkownikiem, ale **jest właścicielem bazy aplikacji**. To wystarcza do wszystkiego, co robią migracje: `CREATE EXTENSION pg_trgm` i `CREATE EXTENSION unaccent` (oba rozszerzenia są „zaufane” od PostgreSQL 13, więc właściciel bazy może je utworzyć; pliki rozszerzeń są już w obrazie `db`) oraz `CREATE TEXT SEARCH DICTIONARY` / `CREATE TEXT SEARCH CONFIGURATION` (wymagają `CREATE` na schemacie `public`, które właściciel bazy ma od PostgreSQL 15). Jeśli bazę zakłada ktoś inny, nadaj: `GRANT CREATE ON DATABASE <baza> TO <użytkownik>; GRANT CREATE ON SCHEMA public TO <użytkownik>;`. `make test-db` tworzy obiekty wyszukiwania właśnie jako taki zwykły użytkownik, więc regresja wyjdzie w testach. Przy pierwszym starcie z pustym wolumenem `pgdata` obraz `db` tworzy tego użytkownika i bazę z `APP_DB_USER`, `APP_DB_PASSWORD` i `APP_DB_NAME` (`docker/db/initdb/10-app-role.sh`). Późniejsza zmiana tych zmiennych **nie** zmienia istniejącej bazy; hasło zmień przez `ALTER ROLE` jako `postgres` i jednocześnie zaktualizuj `.env`.

**Kolizje sieci:** stos używa stałej podsieci `DOCKER_SUBNET` (domyślnie `172.30.70.0/24`). Jeśli sieć firmowa albo trasa VPN używa tego samego zakresu, hosty z niej staną się nieosiągalne z serwera (ruch trafi do mostka Dockera), a `docker compose up` może zakończyć się błędem `Pool overlaps with other one on this address space`. Przed pierwszą instalacją sprawdź to z administratorem sieci i porównaj z `ip route` na serwerze. Aby zmienić, ustaw w `.env` zarówno `DOCKER_SUBNET`, jak i `PROXY_IP` (w nowej podsieci), potem `docker compose down && docker compose up -d` (sieć jest tworzona od nowa; wolumeny zostają).

### Połączenia z bazą i pamięć

Każde połączenie z PostgreSQL ładuje polski słownik przy pierwszym użyciu i trzyma go w pamięci: około 27–30 MB na połączenie (zmierzone w spike'u, zob. [search.md](search.md)). Dlatego limit połączeń jest ustawiony jawnie, a limit pamięci `db` z niego wynika:

- Budżet połączeń: app 5 + 5 nadmiarowych na proces aplikacji, worker 2 na proces workera (zadanie oraz indeksowanie wyszukiwania, które działa, gdy dostarczanie z outboxu trzyma swój wiersz; przy 1 indeksowanie czekałoby na połączenie, które się nie zwolni), `ocr-worker` 2 (jeden proces), plus kilka na migracje, backupy i ręczny `psql`. Przy 2 procesach app i 2 procesach workera: 2 × 10 + 2 × 2 + 2 + 6 = 32, więc domyślne `POSTGRES_MAX_CONNECTIONS=40` zostawia zapas.
- Pamięć: `DB_MEMORY_LIMIT` ≥ `POSTGRES_SHARED_BUFFERS` + `POSTGRES_MAX_CONNECTIONS` × 35 MB + 300 MB. Domyślnie: 256 MB + 40 × 35 MB + 300 MB ≈ 1,95 GB → `DB_MEMORY_LIMIT=2g`.
- Jeśli zwiększasz `POSTGRES_MAX_CONNECTIONS`, zwiększ `DB_MEMORY_LIMIT` o 35 MB na każde dodatkowe połączenie. Inaczej jądro może zabić PostgreSQL (`docker compose ps` pokazuje restartujący się `db`; `docker inspect baza-wiedzy-db-1 --format '{{.State.OOMKilled}}'` wypisuje `true`).
- Sprawdzenie aktualnych wartości: `docker compose exec db psql -U postgres -c 'SHOW max_connections' -c 'SELECT count(*) FROM pg_stat_activity'`.

## Pierwsza instalacja

1. Skopiuj `docker-compose.yml`, `.env.example` i `scripts/` do `/srv/baza-wiedzy/`; zmień nazwę `.env.example` na `.env` i wypełnij; `chmod 600 .env`.
2. Zaloguj się do rejestru. Pakiety GHCR są **domyślnie prywatne**, więc serwer potrzebuje tokenu: utwórz osobisty token dostępu GitHub (classic) wyłącznie z zakresem `read:packages`, najlepiej dla osobnego konta z prawem odczytu repozytorium, a potem na serwerze: `echo "<token>" | docker login ghcr.io -u <użytkownik-github> --password-stdin` (oczekiwane: `Login Succeeded`; dane logowania trafiają do `~/.docker/config.json` użytkownika uruchamiającego Dockera, chroń ten plik). Bez tego `docker compose pull` kończy się błędem `denied` albo `unauthorized`. Odnów token przed wygaśnięciem; to samo dotyczy maszyny budującej pakiet offline.
3. Ustaw `APP_VERSION` i `IMAGE_PREFIX`, potem uruchom: `docker compose pull && docker compose up -d`. Wszystkie cztery obrazy projektu (`app`, `worker`, `db`, `proxy`) pochodzą z GHCR; nic nie jest budowane na serwerze.
4. Uruchom migracje: `docker compose run --rm app baza-wiedzy migrate`. Worker uruchomiony w kroku 3 na to czeka: dopóki schemat nie jest na wszystkich głowach migracji, loguje `worker startup: waiting for database migrations (schema: not migrated); run baza-wiedzy migrate` (raz na minutę), a swoje zadania startowe (odzyskiwanie, outbox) wykonuje dopiero potem. To samo dotyczy środowiska deweloperskiego: `make up`, a potem `make migrate`.
5. Utwórz pierwszego admina: `docker compose exec app baza-wiedzy create-admin bw-admin` (dwa razy pyta o hasło; 14–256 znaków), potem zaloguj się na `https://<PUBLIC_HOSTNAME>/login`.
6. Sprawdź, czy `https://<PUBLIC_HOSTNAME>/ready` zwraca OK.
7. Zaloguj się jako admin, skonfiguruj mapowanie grup AD, przetestuj logowanie z AD.
8. Zaplanuj backupy (patrz niżej).

## Wydania

- Wersjonowanie: SemVer. Tag Gita `vX.Y.Z` na `main` uruchamia `.github/workflows/release.yml`. Pierwsze wydanie: `v0.1.0` (2026-11-12).
- Przed otagowaniem: ustaw `version = "X.Y.Z"` w `pyproject.toml` (aplikacja czyta stamtąd swoją wersję), przenieś `[Unreleased]` w obu dziennikach zmian pod `## [X.Y.Z] - RRRR-MM-DD`, dodaj nową pustą sekcję `[Unreleased]`, zatwierdź, potem `git tag vX.Y.Z && git push origin vX.Y.Z`. Najpierw sprawdź lokalnie: `uv run python scripts/check_release.py vX.Y.Z` musi wypisać `Release vX.Y.Z matches pyproject.toml and both changelogs.`
- Workflow (1) odmawia wydania, jeśli otagowany commit nie należy do `main` (`git merge-base --is-ancestor`; taguj commit `main` po scaleniu PR wydania, a nie commit gałęzi roboczej), jeśli tag różni się od `pyproject.toml` albo jeśli dziennik zmian nie ma sekcji `## [X.Y.Z]`, (2) uruchamia pełne CI (łącznie z testem dymnym), (3) buduje i wypycha cztery obrazy dla `linux/amd64`: `ghcr.io/<właściciel>/baza-wiedzy-app`, `-worker`, `-db`, `-proxy`, wszystkie z tagiem `X.Y.Z` (bez `latest`). Każdy obraz ma etykiety `org.opencontainers.image.version`, `.revision` (SHA commita), `.source` i `.created`. Podsumowanie przebiegu wypisuje każdy obraz z digestem; zapisz je w informacji o wydaniu.
- Co działa na serwerze: `docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' <obraz>`.
- Bezpieczeństwo workflow: akcje są przypięte do SHA commitów; domyślny token ma tylko odczyt (`contents: read`), wyłącznie zadanie publikacji w `release.yml` dostaje `packages: write`; CI nie używa sekretów, więc pull requesty z forków nie mają czego wykraść.

CI (`.github/workflows/ci.yml`, przy pushu na `main` i przy pull requestach) uruchamia trzy zadania: `quality` (`make lint`: ruff, mypy, import-linter; `make test`; `make docs-check`), `integration` (`make test-db`: PostgreSQL z obrazu projektu i Redis) oraz `images` (`make smoke`: buduje cztery obrazy dla `linux/amd64`, uruchamia `docker-compose.yml`, migruje, wymaga, żeby `/ready` zwrócił 4 × `ok`, potem tworzy admina awaryjnego i sprawdza przez proxy: złe hasło 401, logowanie, panel admina i dziennik audytu 200, wylogowanie). Przed pushem uruchom to samo lokalnie.

## Aktualizacja

```
cd /srv/baza-wiedzy
./scripts/update.sh 1.2.0
```

Co robi `update.sh`:
1. Kontrole wstępne: Docker działa, jest wolne miejsce na dysku, nowe obrazy istnieją w rejestrze, `.env` zawiera wszystkie zmienne z nowego `.env.example` (w przeciwnym razie zatrzymuje się i wypisuje brakujące).
2. Uruchamia `backup.sh` (baza + pliki).
3. Pobiera nowe obrazy.
4. Zatrzymuje `worker`, żeby podczas migracji nie szły żadne zadania (worker uruchomiony przed końcem migracji czeka na nią przed zadaniami startowymi, zobacz Pierwsza instalacja).
5. Uruchamia migracje w jednorazowym kontenerze (`baza-wiedzy migrate`).
6. Ustawia `APP_VERSION` w `.env` i uruchamia wszystko (`docker compose up -d`).
7. Czeka na `/ready` (do 120 s).
8. Przy porażce: automatyczny rollback do poprzedniej wersji obrazów i komunikat, czy trzeba odtworzyć bazę.
9. Dopisuje wynik do `logs/update.log`.

Migracje stosują zasadę expand/contract, więc poprzednia wersja aplikacji zwykle nadal działa z nowym schematem. Jeśli wydanie zawiera migrację niezgodną wstecz, notatki wydania o tym informują, a rollback wymaga odtworzenia backupu.

### Rollback

```
./scripts/rollback.sh 1.1.0
```

Przełącza `APP_VERSION` z powrotem i restartuje. Jeśli schemat zmienił się niezgodnie, odtwórz backup sprzed aktualizacji (patrz Odtwarzanie).

### Serwer offline

Na maszynie z dostępem do internetu i po `docker login ghcr.io` (token z `read:packages`, zob. Pierwsza instalacja), z kopii repozytorium na tagu wydania:

```
git checkout v1.2.0
./scripts/offline_bundle.sh 1.2.0 ghcr.io/<właściciel>/baza-wiedzy /tmp/bundle
```

Skrypt bierze listę obrazów z `docker-compose.yml` (z profilem `antivirus`), pobiera wszystko dla `linux/amd64` i zapisuje do jednego pliku. Dla wersji `X.Y.Z` pakiet zawiera **wszystkie** obrazy potrzebne na serwerze:

| Obraz | Odwołanie |
|---|---|
| app | `ghcr.io/<właściciel>/baza-wiedzy-app:X.Y.Z` (digest w podsumowaniu wydania) |
| worker | `ghcr.io/<właściciel>/baza-wiedzy-worker:X.Y.Z` |
| db | `ghcr.io/<właściciel>/baza-wiedzy-db:X.Y.Z` |
| proxy | `ghcr.io/<właściciel>/baza-wiedzy-proxy:X.Y.Z` |
| redis | `redis:8.2.3-alpine@sha256:08ad0b1d280850169a790dba1393ff7a90aef951fc19632cf4d3ce4f78e679ba` |
| clamav (profil opcjonalny) | `clamav/clamav:1.4.3@sha256:75fb5fd95fcbe1d7e6d240c369c1572b686ee2c95949d1042b5148de8eddebb4` |

Wynik: `baza-wiedzy-images-X.Y.Z.tar` (około 0,7 GB), `.tar.sha256` i `baza-wiedzy-images-X.Y.Z.txt` (każde odwołanie z identyfikatorem obrazu). Skopiuj te trzy pliki oraz `docker-compose.yml`, `.env.example` i `scripts/` z wydania na serwer, potem:

```
sha256sum -c baza-wiedzy-images-X.Y.Z.tar.sha256     # oczekiwane: OK
docker load -i baza-wiedzy-images-X.Y.Z.tar          # 6 linii "Loaded image"
docker compose up -d --pull never
```

Później `update.sh` z `SKIP_PULL=1` (Faza 6). Wymaganie: Docker Engine z magazynem obrazów containerd (domyślny w nowych instalacjach Docker Engine 29+; sprawdzenie: `docker info -f '{{.DriverStatus}}'` pokazuje `io.containerd.snapshotter.v1`). Odwołania `redis` i `clamav` przypięte digestem są rozwiązywane z załadowanych obrazów tylko przy tym magazynie; przy starszym `up --pull never` kończy się dla nich błędem `No such image`.

### Po zmianie konfiguracji wyszukiwania

Uruchom `docker compose exec app baza-wiedzy reindex-search --all` (zobacz [search.md](search.md#reindeksacja)); wypisuje `Indexed N version(s); removed 0 orphaned; took T s.`, potem sprawdź `docker compose exec app baza-wiedzy search-check` (`Search index OK.`).

Dotyczy to każdej zmiany polskiego słownika, listy stop words, konfiguracji wyszukiwania tekstowego lub reguł identyfikatorów w `search/identifiers.py`, także nowego obrazu `db` z inną wersją `hunspell-pl` albo `polish.stop` (dziennik zmian wtedy o tym informuje). Istniejące wartości `tsv_*` zostały wyliczone starym słownikiem i nie będą pasować do zapytań przetworzonych nowym, dopóki nie przebudujesz indeksu. Restart `db` zrywa też wszystkie połączenia, więc każde połączenie załaduje nowy słownik przy pierwszym użyciu.

## Backup

`scripts/backup.sh` (Faza 6) tworzy `/srv/baza-wiedzy/backups/RRRR-MM-DD_GGMMSS/` zawierający:
- `db.dump` — `docker compose exec -T db pg_dump -Fc -U postgres <APP_DB_NAME>` (spójny zrzut przy działającej aplikacji)
- `storage.tar` — wolumen `storage` (pliki oryginalne i pochodne), czytany przez krótko żyjący kontener: `docker run --rm -v baza-wiedzy_storage:/data/storage:ro -v "$BACKUP_DIR":/backup <obraz app> tar -C /data -cf /backup/storage.tar storage`
- `env.backup` — kopia `.env` (zawiera sekrety; zabezpiecz miejsce backupu)
- `SHA256SUMS`

Domyślna retencja: 7 dziennych, 4 tygodniowe, 6 miesięcznych (`BACKUP_KEEP_*`). Kopiowanie backupów poza serwer (NAS, inny serwer, magazyn obiektowy) to hak: ustaw `BACKUP_REMOTE_CMD` na polecenie, które dostaje ścieżkę katalogu backupu. Miejsce docelowe zostanie ustalone później; do tego czasu backupy leżą tylko na tym samym serwerze i to **nie** jest wystarczający backup.

Harmonogram (cron, codziennie w nocy):

```
15 2 * * * /srv/baza-wiedzy/scripts/backup.sh >> /srv/baza-wiedzy/logs/backup.log 2>&1
```

Wolumenów Dockera nie kopiuje się bezpośrednio (kopia `pgdata` działającej bazy nie jest spójna); baza idzie przez `pg_dump`, pliki przez `tar`. Redis przechowuje tylko kolejkę zadań; nie wymaga backupu (niedokończone zadania są kolejkowane ponownie przez `baza-wiedzy reprocess --pending`).

## Odtwarzanie

```
./scripts/restore.sh /srv/baza-wiedzy/backups/2026-11-12_021500
```

Zatrzymuje `app` i `worker`, odtwarza bazę (`pg_restore --clean --if-exists`), odtwarza pliki, uruchamia usługi i sprawdza liczbę dokumentów i plików.

**Test odtwarzania:** raz w miesiącu odtwórz ostatni backup na zapasowej maszynie lub w tymczasowym projekcie Compose i wykonaj kilka wyszukiwań. Backup, który nigdy nie został odtworzony, nie jest backupem.

## Monitoring

| Endpoint | Znaczenie |
|---|---|
| `/health` | Proces żyje (bez sprawdzania zależności) |
| `/ready` | Baza osiągalna, Redis osiągalny, świeży heartbeat workera, schemat zmigrowany dla włączonych modułów. JSON; HTTP 200, gdy każde sprawdzenie ma wartość `ok`, w przeciwnym razie 503 |

Przykład poprawnej odpowiedzi (`curl -k https://<PUBLIC_HOSTNAME>/ready`):

```
{"status":"ok","version":"1.0.0","checks":{"database":"ok","redis":"ok","schema":"ok","worker":"ok"}}
```

| Sprawdzenie | Wartość inna niż `ok` | Znaczenie i co zrobić |
|---|---|---|
| `database` | `unreachable` | `docker compose ps db`, `docker compose logs db`; sprawdź `APP_DB_PASSWORD` |
| `redis` | `unreachable` / `authentication failed` | `docker compose ps redis`; `REDIS_PASSWORD` w `.env` różni się od tego, z którym wystartował Redis: `docker compose up -d redis app worker` |
| `schema` | `not migrated` / `outdated` / `unknown` | Uruchom `docker compose run --rm app baza-wiedzy migrate`. `ok (schema newer than application)` jest normalne przez chwilę podczas rollbacku |
| `worker` | `no heartbeat` | Żaden proces workera nie zapisał heartbeatu w ostatnich 60 s: `docker compose ps worker`, `docker compose logs worker` |

Sprawdzenie kolejki od początku do końca: `docker compose exec worker baza-wiedzy worker-ping` wysyła zadanie testowe i wypisuje `Worker answered in 0.6 s.` (kod wyjścia 0) albo `No answer from a worker within 10.0 s.` (kod wyjścia 1).

Zabbix: sprawdzenie HTTP na `/ready` (oczekiwane 200) oraz trigger na czas odpowiedzi. Endpoint z metrykami i długość kolejki to opcjonalne dodatki na później.

Logi to linie JSON, jeden obiekt na linię: `{"ts": "2026-11-12T11:10:39.174+00:00", "level": "INFO", "logger": "uvicorn.access", "message": "10.0.0.5:0 - \"GET /ready HTTP/1.1\" 200", "process": 8}`; błędy mają dodatkowo `exc` ze śladem stosu. `app` pisze na stdout, procesy workera na stderr. Wyjątek: proces główny Dramatiq w `worker` wypisuje swoje nieliczne linie startu i zatrzymania we własnym formacie tekstowym Dramatiq (`[2026-11-12 11:10:33,910] [PID 1] [MainThread] [dramatiq.MainProcess] [INFO] ...`). `LOG_LEVEL` ustawia poziom (domyślnie `INFO`). Compose ustawia rotację logów Dockera (`max-size 10m`, `max-file 5`). Czytaj je przez `docker compose logs -f app worker`; filtruj przez `jq`, na przykład `docker compose logs --no-log-prefix app | jq -R 'fromjson? | select(.level=="ERROR")'`.

## Przetwarzanie dokumentów (przewodnik operatora)

Co działa gdzie: aplikacja webowa (`app`) zapisuje wgrane pliki i je kolejkuje; `worker` wyciąga tekst w tle; `ocr-worker` rozpoznaje zeskanowane strony. Szczegóły i reguły: [ingestion-and-ocr.md](ingestion-and-ocr.md).

**Start i migracje.** Kolejność na nowym serwerze i w środowisku deweloperskim: uruchom stos (`docker compose up -d` / `make up`), potem migruj (`docker compose run --rm app baza-wiedzy migrate` / `make migrate`). Worker uruchomiony przed migracją raz na minutę loguje `worker startup: waiting for database migrations`, a zadania startowe wykonuje potem: usuwa pozostałe katalogi tymczasowe, kolejkuje oczekujące wersje, odzyskuje zawieszone, dostarcza niedostarczone zdarzenia.

**Statusy** widoczne w „Moje dokumenty” i na stronie dokumentu:

| Status | Znaczenie | Co zrobić |
|---|---|---|
| oczekuje | w kolejce | nic; jeśli trwa: czy worker działa (`docker compose ps`)? |
| przetwarzanie | trwa próba | nic; po `INGEST_STALE_MINUTES` (30) bez postępu wersja jest odzyskiwana przy najbliższym starcie workera albo przez `baza-wiedzy reprocess --stuck` |
| gotowe | tekst wyciągnięty | — |
| wymaga OCR | strony z tekstem wyciągnięte i wyszukiwalne, co najmniej jedna zeskanowana strona czeka na OCR; druga etykieta mówi, gdzie: „OCR w kolejce”, „OCR w toku”, „OCR wstrzymany (duży dokument)”, „OCR nieudany”, „OCR pominięty (za dużo stron)”, „OCR nieuruchomiony” | w kolejce / w toku: nic (czy `ocr-worker` działa?); wstrzymany: „Uruchom OCR”, gdy serwer nie jest zajęty; nieudany: przeczytaj komunikat, „Uruchom OCR ponownie”; nieuruchomiony (dokumenty sprzed 0.5): `baza-wiedzy ocr --pending` |
| błąd | błąd trwały (uszkodzony, zabezpieczony hasłem, nieznane kodowanie, limit) albo błędy przejściowe we wszystkich próbach | komunikat na stronie mówi, który; popraw plik i wgraj go ponownie albo po usunięciu przyczyny „Przetwórz ponownie” |

**Ponawianie**: tylko błędy przejściowe (baza, Redis, limity czasu), do `INGEST_MAX_ATTEMPTS` (3) razy, po 30 s, 60 s, 120 s… (najwyżej 600 s). Błędy trwałe nie są ponawiane.

**LibreOffice (DOC)**: jedna konwersja naraz na kontener workera, każda najwyżej `LIBREOFFICE_TIMEOUT_SECONDS` (120 s) i `LIBREOFFICE_MEMORY_MB` (1 GB); parsowanie PDF też działa w procesie potomnym (`INGEST_CHILD_*`). Ani worker, ani te procesy nie mają dostępu do internetu (ADR-024).

**Pamięć workera**: zmierzona przy 2 procesach 173 MiB w spoczynku, poniżej 0,5 GB pod obciążeniem. Reguła: `WORKER_MEMORY_LIMIT` ≥ 1,4 GB + 0,6 GB × `WORKER_PROCESSES` (domyślnie 2 procesy, 2 GB). Więcej procesów niż rdzeni CPU nie pomaga.

**OCR** działa w usłudze `ocr-worker` (ten sam obraz co `worker`, `dramatiq … --queues ocr --processes 1 --threads 1`), zwykły `worker` obsługuje tylko kolejkę `ingestion`: wgranie jest przetwarzane od razu nawet w trakcie długiego OCR. Jeden dokument OCR naraz, w środku `OCR_JOBS` procesów Tesseracta, limity kontenera `OCR_MEMORY_LIMIT` (3g) i `OCR_CPUS` (2); tylko sieć wewnętrzna. Małe skany (do `OCR_AUTO_MAX_PAGES`, 30 stron) startują same, najkrótsze najpierw; większe czekają na „Uruchom OCR”. Jego wiersze logu: `ocr: start version=… pages=…`, `ocr: done version=… pages=… recognised=… left=… seconds=…`, `ocr: failed version=… reason=timeout`; nigdy tekst. Zatrzymanie `ocr-worker` (`docker compose stop ocr-worker`) tylko wstrzymuje OCR: dokumenty w kolejce czekają, wgrywanie działa; przy następnym starcie wiadomości utracone w międzyczasie są wysyłane ponownie.

**Polecenia** (w kontenerze `app`: `docker compose exec app baza-wiedzy …`):

| Polecenie | Działanie | Oczekiwany wynik |
|---|---|---|
| `reprocess <ID dokumentu lub wersji> …` | przetwórz ponownie (audyt `document.reprocess`) | `Queued for processing again: 1; skipped (busy): 0.` |
| `reprocess --failed` | każda wersja ze statusem „błąd” | jak wyżej, z liczbą |
| `reprocess --stuck` | zawieszone i oczekujące wersje z powrotem do kolejki (bez audytu) | `Stuck versions: 0 queued again, 0 failed (no attempts left); waiting versions queued again: 0.` |
| `events redeliver` | dostarcz niedostarczone zdarzenia, usuń stare dostarczone | `Events delivered: 0, failed: 0, still undelivered: 0; old delivered entries removed: 0.` |
| `ocr --pending [--max-pages N]` | uruchom OCR każdej wersji „wymaga OCR” nigdy nieuruchomionej albo wstrzymanej (na przykład dokumentów sprzed 0.5), z `--max-pages` tylko do N zeskanowanych stron; audyt `document.ocr_request` (`source: cli`); po aktualizacji nie ma automatycznej fali | `Queued for OCR: 3; nothing to do: 0.` oraz `Left out (more scanned pages than --max-pages 30): 1.` |
| `ocr <ID dokumentu lub wersji> …` | uruchom OCR tych dokumentów (wstrzymanych, nieudanych, nieuruchomionych) | `Queued for OCR: 1; nothing to do: 0.` |
| `storage-cleanup [--older-than GODZINY]` | usuń pliki przerwanych wgrań | `Removed 0 orphaned incoming file(s) from /data/storage/.incoming.` |
| `search-check` | porównaj indeks wyszukiwania z wyodrębnionym tekstem (tylko identyfikatory; kod 1 przy różnicy) | `Extracted versions: 26` … `Search index OK.` |
| `reindex-search --missing` | zaindeksuj to, co zgłasza `search-check`, usuń osierocone wiersze | `Indexed 1 version(s); removed 0 orphaned; took 0.2 s.` |
| `reindex-search --all` | przebuduj cały indeks (po zmianie słownika albo konfiguracji) | ta sama linia z sumą |
| `reindex-search --document ID` | jeden dokument ponownie | ta sama linia |

**Wyszukiwanie.** Po aktualizacji do 0.4.0 jeden proces workera przy pierwszym starcie indeksuje wszystkie wcześniej przetworzone dokumenty (linia logu `search: indexed N version(s) missing from the index`; pozostałe procesy logują `search: startup indexing runs in another worker process; skipped here`; dla 200 dokumentów kilka sekund); `search-check` musi potem wypisać `Search index OK.`. Nowe dokumenty są indeksowane zaraz po przetworzeniu. Publikacja, cofnięcie publikacji, archiwizacja i przywrócenie nie wymagają reindeksu. Każde wyszukiwanie zapisuje w logu jedną linię z samymi licznikami (`search: results=… duration_ms=…`), nigdy frazy. Wyszukiwanie wolniejsze niż `SEARCH_TIMEOUT_MS` jest pokazywane według daty zamiast trafności (`fallback=yes` w logu); jeśli zdarza się często, zobacz uwagi o wydajności w [search.md](search.md#cel-wydajnościowy).

Próbki do ręcznego sprawdzenia serwera albo nowej wersji: `make probki DIR=<katalog>` na komputerze deweloperskim (oczekiwane wyniki w `OCZEKIWANE.txt`; próbki wyszukiwania w `wyszukiwanie/` z `OCZEKIWANE-wyszukiwanie.txt`).

## Polecenia dla programisty

Wszystko idzie przez `Makefile` (z Git Basha na Windows lub z powłoki Linuksa). Python 3.12 i środowisko wirtualne zarządza `uv`; uruchom raz `uv sync --all-extras` (dodatek `worker` zawiera parsery dokumentów potrzebne testom i sprawdzaniu typów; `make lint` i `make test` dodają go same).

**Dane testowe.** Dane w repozytorium są wyłącznie syntetyczne. Numery telefonów z puli 600 100 xxx, adresy e-mail i hosty w domenach zarezerwowanych (example.com, example.org), NIP-y i numery umów wymyślone. Żadnych nazw firm, numerów ani fragmentów z prawdziwych dokumentów. Prawdziwe dokumenty i zapytania do testu trafności zostają poza repozytorium (`make relevance DIR=… QUERIES=…` poza drzewem repo).

| Polecenie | Co robi |
|---|---|
| `make help` | Wypisuje wszystkie polecenia |
| `make lint` | `ruff`, `mypy`, kontrola granic modułów (`lint-imports`) i `make lint-workflows`: actionlint (ze shellcheck dla skryptów `run:`) na `.github/workflows/*.yml`, z obrazu Dockera przypiętego do digestu, więc Docker jest wymagany. Strukturę workflowów i `dependabot.yml` sprawdza też bez Dockera `tests/unit/test_github_config.py` (`make test`). Błąd YAML w workflow sprawia, że GitHub odrzuca cały plik i nie uruchamia żadnego zadania („Invalid workflow file”), więc nazwę kroku zawierającą `: ` albo ` #` ujmuj w cudzysłów |
| `make lint-workflows` | Tylko sprawdzenie workflowów przez actionlint |
| `make fmt` | Formatuje i naprawia automatycznie przez `ruff` |
| `make test` | Uruchamia testy `pytest` (testy integracyjne i wzorcowe wyszukiwania są pomijane) |
| `make smoke` | Test dymny produkcyjnego pliku Compose: buduje cztery obrazy (`linux/amd64`), uruchamia `docker-compose.yml` jako projekt `baza-wiedzy-smoke` na portach 18080/18443 z wygenerowanymi sekretami, migruje, wymaga `/ready` = 4 × `ok`, sprawdza izolację sieci (worker sięga do db i redis, do niczego poza nimi), sprawdza, że obraz app nie zawiera bs4 ani soupsieve, a worker ma soupsieve >= 4.0, potem tworzy admina awaryjnego `smoke-admin` z wygenerowanym hasłem i sprawdza przez proxy: panel admina bez sesji 303, złe hasło 401, logowanie 303, `/admin/users` i `/admin/audit` 200, wgranie małego PDF z tekstem i pliku TXT o nazwie `smoke Łódź.txt` (po 204) i oczekiwanie, aż worker przetworzy oba („gotowe”, najwyżej 2 minuty każdy), strony części 200, publikację TXT 303, jego pobranie (te same bajty; `application/octet-stream`, `nosniff`, `attachment` z nazwą ASCII i UTF-8, `CSP sandbox`), potem unikalną frazę w parametrach zapytania, `Referer` i `HX-Current-URL`, która nie może się pojawić w żadnym logu proxy, app, worker i db (a log proxy nie może zawierać nazwy pliku ani tokenu CSRF), wylogowanie 303. Na koniec sprząta wszystko. Oczekiwany koniec: `==> Smoke test passed: /ready 4 x ok, network isolation, login, admin panel, TXT and PDF processed to "ready", publish and download, no search phrase in logs, logout` (około 1–2 minut przy zbuforowanych warstwach) |
| `make test-db` | Uruchamia testy integracyjne i wzorcowe wyszukiwania w Dockerze: jednorazowy PostgreSQL z `docker/db` na tmpfs i jednorazowy kontener testowy w prywatnej sieci Compose (`docker-compose.test.yml`); żaden port nie jest publikowany. Kontener testowy to etap `test` obrazu workera (LibreOffice, Tesseract; ADR-023) i uruchamia znaczniki `integration` i `tools`. Oczekiwany koniec (Faza 2): `… passed, 2 skipped`. Pierwsze uruchomienie buduje obraz testowy (około 2 minut) i pobiera pakiety Pythona do wolumenu Dockera |
| `make docs-check` | Sprawdza spójność dokumentacji angielskiej i polskiej |
| `make messages` | Wyciąga teksty interfejsu do `messages.pot` i aktualizuje polski katalog `messages.po`; potem uzupełnij nowe pozycje `msgstr` |
| `make up` / `make down` / `make logs` / `make build` | Uruchamia, zatrzymuje, pokazuje logi i buduje stos Dockera (wymaga Dockera) |
| `make migrate` | Stosuje migracje bazy w stosie dev (`baza-wiedzy migrate` w jednorazowym kontenerze `app`). Oczekiwany wynik: `Database schema is up to date (heads: core_0003, documents_0002, ingestion_0001, search_0001).` |
| `make revision branch=<gałąź> m="tekst"` | Tworzy pusty plik migracji w gałęzi rdzenia lub modułu (`core`, `search`, ...), na hoście, bez bazy |
| `make shell` | Powłoka w kontenerze `app` (wymaga Dockera) |
| `make probki DIR=<katalog>` | Zapisuje do katalogu syntetyczne pliki do ręcznego testu każdego formatu i przypadku błędu (TXT cp1250 i cp852, DOCX, DOC, PDF z tekstem, PDF skan, PDF zaszyfrowany, EML i MSG z załącznikami, pliki uszkodzone) z plikiem `OCZEKIWANE.txt` opisującym oczekiwany wynik każdego; podkatalog `wyszukiwanie/` zawiera korpus wyszukiwania (26 dokumentów polskich i angielskich, tych samych co w testach golden wyszukiwania, `tests/fixtures/search_corpus.py`), a `OCZEKIWANE-wyszukiwanie.txt` podaje ich docelowy stan (publikacja, szkic, archiwum) i zapytania z oczekiwanymi dokumentami; działa w obrazie testowym (LibreOffice dla DOC) |
| `make perf N=20000` | Wydajność wyszukiwania na N syntetycznych dokumentach (ręcznie, nigdy w CI; około 10 minut dla 20 000, godzina dla 100 000): jednorazowy PostgreSQL z ustawieniami produkcyjnymi (`docker-compose.perf.yml`), pełne indeksowanie prawdziwym indekserem, rozmiary, p50/p95 zapytań, `EXPLAIN (ANALYZE, BUFFERS)`, 10 równoczesnych wyszukiwań, ocena słownika bez polskich znaków. Wyniki w `perf-results/perf-N.md` i `.json` (ignorowane przez git); aktualne liczby są w [search.md](search.md#cel-wydajnościowy) |
| `make relevance DIR=<dokumenty> QUERIES=<plik> AS=<użytkownik> [TOP=5] [ONLY=1]` | Ocena trafności na własnych dokumentach i zapytaniach na **lokalnym stosie deweloperskim** (najpierw `make up`; użytkownik musi istnieć i móc wgrywać). Wgrywa każdy plik z DIR jako szkic tego użytkownika (ponownie tylko nowe), czeka na przetworzenie, wykonuje każde zapytanie z pliku jako ten użytkownik i drukuje pierwsze TOP wyników z wynikiem, tytułem, nazwą pliku, miejscem trafienia i fragmentem; `ONLY=1` ukrywa inne dokumenty widoczne dla użytkownika. Plik zapytań: UTF-8, jedno zapytanie w linii, komentarze `#`, opcjonalnie `zapytanie => fragment oczekiwanego tytułu albo nazwy pliku` (OK/MISSING). **Repozytorium jest publiczne: DIR i QUERIES muszą być poza nim** (inaczej odmowa; typowe nazwy są w `.gitignore`); nigdy nie commituj, nie wklejaj ani nie załączaj wyniku. Szczegóły: [search.md](search.md#testy-wzorcowe-golden) |
| `make seed-samples` | Zarezerwowane na ładowanie syntetycznych dokumentów przykładowych (jeszcze niedostępne) |

Jeśli `make` jest niedostępny, uruchom te same narzędzia przez `uv run`, na przykład `uv run pytest`.

Znane ostrzeżenia (nieszkodliwe, sprawdzane przy miesięcznym przeglądzie zależności):
- `pytest`: `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.` Starlette 1.10 zapowiada zmianę klienta HTTP używanego przez `TestClient` FastAPI. Testy nie są dotknięte; wrócimy do tego, gdy FastAPI/Starlette opiszą zamiennik.
- `pytest`: `DeprecationWarning: tagMap is deprecated` / `typeMap is deprecated` z `ldap3/utils/asn1.py`: ldap3 2.9.1 używa starych nazw z `pyasn1`. Nieszkodliwe; wrócimy do tego, gdy ldap3 wyda poprawkę.
- `make up`: Caddy loguje `admin endpoint disabled`, `HTTP/2 skipped because it requires TLS` (dla serwera przekierowania na porcie 80) i `stapling OCSP` (certyfikat wewnętrzny). Wszystko oczekiwane.

Zależności: wersje są przypięte w `uv.lock`, a rozwiązywane są tylko wydania starsze niż `exclude-newer` (w `pyproject.toml`). Aktualizacje odbywają się w miesięcznym przeglądzie zależności albo, dla poprawek bezpieczeństwa, jako wyjątek; procedura jest w ADR-015 ([decisions.md](decisions.md)). Dependabot (`.github/dependabot.yml`) co miesiąc otwiera pull requesty dla SHA akcji GitHub, obrazów bazowych w Dockerfile'ach i obrazów w `docker-compose.yml` (tylko wydania starsze niż 14 dni) oraz pull requesty bezpieczeństwa dla paczek Pythona. Obrazy bazowe dostają tylko poprawki w ramach PostgreSQL 16.x i Python 3.12.x: przejście na nową wersję główną PostgreSQL (na przykład 18) albo pomocniczą Pythona (na przykład 3.13) to zaplanowana migracja z własnym ADR i procedurą (zrzut i odtworzenie bazy, ponowny spike słownika z testami wzorcowymi wyszukiwania, `reindex-search`, przegląd zależności), nigdy rutynowa aktualizacja. Dwutygodniowy cooldown Dependabota nie działa dla obrazów Dockera (rejestr nie podaje dat publikacji): w przeglądzie miesięcznym sprawdź każdy PR dla Dockera ręcznie (informacje o wydaniu nowego tagu, co najmniej dwa tygodnie od publikacji, zielone CI) przed scaleniem.

Alert bezpieczeństwa dla paczki Pythona, której Dependabot nie może podnieść z powodu `exclude-newer`: (1) oceń ekspozycję (czy podatny kod jest osiągalny w tej aplikacji? zapisz wniosek, najlepiej jako test); (2) odblokuj tylko tę paczkę: dodaj ją do `exclude-newer-package` w `[tool.uv]` w `pyproject.toml` z datą tuż po wydaniu z poprawką, potem `uv lock --upgrade-package <nazwa>`; (3) sprawdź `git diff uv.lock`: zmienić się może tylko ta paczka (i, jeśli nie da się inaczej, jej bezpośrednie zależności); (4) wpis w `### Security` w `[Unreleased]` obu dzienników zmian i linia w ADR-015; (5) `make lint test test-db docs-check smoke`; (6) usuń wyjątek przy najbliższym przeglądzie miesięcznym. Przykład: alert Dependabota #1 (`opentelemetry-api` 1.45.2) w ADR-015.

Lokalny stos: raz `cp .env.example .env` i ustaw `SECRET_KEY` na wygenerowaną wartość (aplikacja odrzuca wartość zastępczą); pozostałe wartości domyślne wystarczą do rozwoju. Potem `make build` i `make up`. `make up` buduje obrazy lokalnie (`docker-compose.dev.yml`), montuje `src/` w `app` i `worker` i przeładowuje serwer WWW po zmianie. Otwórz `https://localhost`: certyfikat pochodzi z wewnętrznego CA Caddy, więc **przeglądarka pokaże ostrzeżenie o certyfikacie**; zaakceptuj je dla `localhost` (albo zaimportuj certyfikat główny: `docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt .`). Z wiersza poleceń użyj `curl -k https://localhost/`. `http://localhost` odpowiada `301` na `https://localhost/`. Na maszynie z małą ilością pamięci (na przykład WSL2, które domyślnie dostaje połowę RAM-u hosta) obniż limity w deweloperskim `.env`: `POSTGRES_MAX_CONNECTIONS=20`, `DB_MEMORY_LIMIT=1g`, `WORKER_MEMORY_LIMIT=1536m`, `APP_MEMORY_LIMIT=512m`; pamięć dostępną dla Dockera sprawdzisz przez `docker info --format '{{.MemTotal}}'`. Wartości domyślne są dobrane pod serwer. Po pierwszym `make up` uruchom `make migrate`; potem `curl -k https://localhost/ready` zwraca `"status":"ok"` ze wszystkimi czterema sprawdzeniami `ok`. Utwórz lokalnego admina poleceniem `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec app baza-wiedzy create-admin bw-admin` i zaloguj się na `https://localhost/login`. W dev `app` i `worker` poddają się po 3 nieudanych startach (`restart: on-failure:3`) zamiast restartować się w nieskończoność; `make logs` pokazuje przyczynę. Serwer WWW przeładowuje się po zmianie kodu, worker nie: po zmianie kodu zadań uruchom `docker compose -f docker-compose.yml -f docker-compose.dev.yml restart worker`.

## Rozwiązywanie problemów

| Objaw | Prawdopodobna przyczyna | Co sprawdzić |
|---|---|---|
| Dokument jest „gotowe”, ale nie jest znajdowany | Niezaindeksowany (zdarzenie nie zostało dostarczone) albo użytkownik nie może go widzieć (cudzy szkic, zarchiwizowany, przetwarzanie zakończone błędem) | `docker compose exec app baza-wiedzy search-check`; potem `reindex-search --missing`; niedostarczone zdarzenia: `docker compose exec app baza-wiedzy events redeliver`, `docker compose logs worker \| grep outbox` |
| Wyszukiwanie pokazuje „Wyszukiwanie trwało zbyt długo” albo „posortowane według daty” | Bardzo ogólne zapytanie przekroczyło `SEARCH_TIMEOUT_MS` | Normalne dla pojedynczych bardzo częstych słów; częste przy zwykłych zapytaniach: obciążenie bazy (`docker compose exec db psql -U postgres -c 'SELECT count(*) FROM pg_stat_activity'`), pamięć `db`; zobacz [search.md](search.md#cel-wydajnościowy) |
| Wgrane pliki zostają w stanie „oczekuje” (`pending`) | Worker lub Redis nie działa albo zadanie przepadło, gdy Redis był niedostępny | `docker compose ps`, `docker compose logs worker redis`; gdy oba działają, restart workera (`docker compose restart worker`) albo `docker compose exec app baza-wiedzy reprocess --stuck` kolejkuje oczekujące wersje ponownie |
| Dokument wisi w stanie „przetwarzanie” | Worker został zabity w trakcie próby (limit pamięci, restart) | Odczekaj `INGEST_STALE_MINUTES` i zrestartuj workera albo `docker compose exec app baza-wiedzy reprocess --stuck`; w logu workera jest identyfikator wersji (`docker compose logs worker \| grep <id wersji>`) |
| „błąd”: „Nie rozpoznano kodowania tekstu…” | TXT w stronie kodowej innej niż UTF-8, Windows-1250, ISO-8859-2 (na przykład DOS CP852) | Zapisz plik w UTF-8 i wgraj go ponownie (ADR-022) |
| „błąd”: powtarzany „Chwilowy problem…”, potem „Przetwarzanie kilka razy nie powiodło się…” | Baza lub Redis niedostępne dłużej niż trwają ponowienia albo próby dłuższe niż `INGEST_TIME_LIMIT_SECONDS` | Usuń przyczynę (`docker compose logs worker`: klasa wyjątku dla wersji), potem „Przetwórz ponownie” albo `baza-wiedzy reprocess --failed` |
| „błąd”: „Przetwarzanie trwało dłużej niż limit czasu.” dla DOC lub PDF | LibreOffice albo proces potomny PDF przekroczył `LIBREOFFICE_TIMEOUT_SECONDS` / `INGEST_CHILD_TIMEOUT_SECONDS` w każdej próbie (bardzo duży lub uszkodzony plik, przeciążony serwer) | `docker compose logs worker \| grep <id wersji>` (`IsolationTimeout`); jeśli plik jest prawidłowy, zwiększ limit w `.env`, `docker compose up -d worker`, potem „Przetwórz ponownie” |
| „błąd”: „…zabezpieczony hasłem…” | PDF, DOC albo DOCX zabezpieczony hasłem | Wgraj kopię bez hasła; aplikacja nigdy nie pyta o hasła |
| `baza-wiedzy events redeliver` zgłasza nieudane dostarczenia | Handler zdarzenia zgłasza wyjątek (klasa wyjątku w logu) | Popraw handler (błąd w kodzie: zgłoś go), uruchom polecenie ponownie; wpisy czekają do skutecznego dostarczenia |
| „OCR nieudany”: „OCR trwał dłużej niż limit czasu…” | Bardzo duży lub słabej jakości skan, wolny serwer | `docker compose logs ocr-worker` (`ocr: failed … reason=timeout`); zwiększ `OCR_TIMEOUT_SECONDS`, `docker compose up -d ocr-worker`, potem „Uruchom OCR ponownie” |
| „OCR w kolejce” nie rusza dalej | `ocr-worker` nie działa albo Redis zgubił wiadomość | `docker compose ps ocr-worker`, `docker compose logs ocr-worker`; `docker compose restart ocr-worker` wysyła utracone wiadomości ponownie |
| „OCR nieudany”: „OCR został przerwany…” | `ocr-worker` zabity w trakcie OCR (brak pamięci: `docker inspect <kontener ocr-worker> --format '{{.State.OOMKilled}}'`) | Zwiększ `OCR_MEMORY_LIMIT` albo zmniejsz `OCR_JOBS`, potem „Uruchom OCR ponownie” |
| Użytkownicy AD widzą „Logowanie kontem firmowym jest chwilowo niedostępne” | Kontrolery domeny nieosiągalne, problem z certyfikatem albo odrzucone konto serwisowe (audyt `auth.ad_unavailable`, `reason`) | `docker compose logs app \| grep 'Active Directory'`; `docker compose exec app baza-wiedzy ad-check`; przy `tls`: `openssl s_client -connect <dc>:636 -CAfile secrets/ldap/ca.pem` i nazwa hosta w `LDAP_URI`. Admin awaryjny dalej działa |
| `app` kończy się komunikatem `Active Directory configuration: ...` | `LDAP_ENABLED=true` przy niepełnej konfiguracji (brak pliku CA, adres `ldap://`, pusty bind DN) | Popraw zmienną wskazaną w komunikacie albo ustaw `LDAP_ENABLED=false` |
| Jeden użytkownik AD nie może się zalogować, inni mogą | Złe hasło, konto wyłączone lub wygasłe w AD, nazwa zajęta przez konto lokalne, nazwa poza `LDAP_BASE_DN` | `docker compose exec app baza-wiedzy ad-check --user <nazwa> --password`; audyt `auth.login_failed` `reason`; `docker compose logs app` pod kątem `AD login refused` |
| Odmienione słowa nie są znajdowane | Słownik niezaładowany lub indeks nieprzebudowany | `SELECT ts_lexize('pl_hunspell','faktury');`, uruchom `reindex-search` |
| `/ready` nie działa po aktualizacji | Migracja nie została zastosowana | `docker compose run --rm app baza-wiedzy migrate`, potem sprawdź `update.log` |
| Pełny dysk | Backupy lub pliki pochodne | Sprawdź `backups/`, ustawienia retencji |
| `could not open dictionary file ".../pl_pl.dict"` | Baza działa na zwykłym obrazie `postgres` zamiast obrazu `db` projektu | `docker compose images db`; przebuduj lub pobierz obraz `db` |
| `app` kończy się komunikatem `SECRET_KEY is missing or too weak` | `SECRET_KEY` krótszy niż 32 znaki albo wartość zastępcza | Wygeneruj klucz (zob. Konfiguracja), wpisz do `.env`, `docker compose up -d app` |
| „Zbyt wiele nieudanych prób” (HTTP 429) przy logowaniu | Blokada po nieudanych logowaniach dla nazwy i/lub adresu | Odczekaj `LOGIN_LOCKOUT_MINUTES` albo `docker compose exec app baza-wiedzy unlock-login --user <nazwa>` / `--ip <adres>` |
| `docker compose up`: `Pool overlaps with other one on this address space` | `DOCKER_SUBNET` koliduje z inną siecią Dockera albo trasą | Wybierz inne `DOCKER_SUBNET` i `PROXY_IP` (Konfiguracja) |
| Strona 403 „Żądanie odrzucone” | Strona otwarta długo, w międzyczasie zmiana `SECRET_KEY` albo nowa sesja, albo zablokowane żądanie z innej witryny | Odśwież stronę i powtórz czynność; powód jest w logu: `docker compose logs app \| grep 'CSRF check refused'` (`missing_token` przy każdym wysłaniu jednego formularza = błąd szablonu, zgłoś go) |
| Wgranie kończy się komunikatem „Plik jest większy niż dozwolony rozmiar” albo „Pliki są razem większe…” (413) albo stroną proxy „Za duże żądanie” | Plik większy niż `MAX_UPLOAD_MB`, pliki razem większe niż `UPLOAD_MAX_TOTAL_MB` albo `MAX_REQUEST_MB` (proxy) mniejszy | Wgraj w częściach albo zwiększ limity w `.env` (`MAX_REQUEST_MB` = `UPLOAD_MAX_TOTAL_MB` + 1), `docker compose up -d proxy app` |
| W `/data/storage/.incoming` gromadzą się pliki | Przerwane wgrania (zamknięta przeglądarka, restart aplikacji) | Nieszkodliwe; usuwane po godzinie przy następnym wgraniu albo od razu: `docker compose exec app baza-wiedzy storage-cleanup` (oczekiwane: `Removed N orphaned incoming file(s) ...`) |
| Aplikacja nie startuje: `module ... requires ...` | `ENABLED_MODULES` zawiera moduł bez modułu, od którego zależy | Dodaj wymagany moduł do `ENABLED_MODULES` w `.env` albo usuń moduł zależny |

## Uwagi bezpieczeństwa dla operatorów

- Wystawione są tylko porty 80/443. Baza i Redis nie są osiągalne z sieci; Redis wymaga też hasła (`REDIS_PASSWORD`).
- `worker`, `db` i `redis` są wyłącznie w wewnętrznej sieci Dockera `backend` (bez drogi do internetu, bez rozwiązywania publicznych nazw); `app` jest w niej i w `default` (dostęp z proxy, wychodzące LDAPS do Active Directory). Healthchecki działają wewnątrz kontenerów i zmiana ich nie dotyczy; `scripts/offline_bundle.sh` wymienia tylko obrazy i nie wymaga zmian; przyszły `update.sh` (Faza 6) uruchamia `docker compose`, który tworzy albo aktualizuje obie sieci. Sprawdzenie: `make smoke` („Network isolation”) albo na serwerze `docker compose exec worker python -c "import socket; socket.create_connection(('1.1.1.1', 443), 3)"` musi się skończyć `OSError` (sieć nieosiągalna).
- Kontenery działają bez roota: `app` i `worker` jako uid 10001, `proxy` jako uid 10002 (nasłuch 8080/8443 wewnątrz, bez capabilities), `db` i `redis` przechodzą na własnych użytkowników po starcie. `app`, `worker` i `proxy` odrzucają wszystkie capabilities Linuksa; wszystkie usługi mają `no-new-privileges`.
- Logi Dockera są rotowane: najwyżej 5 plików × 10 MB na kontener.
- Logi nigdy nie zawierają wyszukiwanych fraz, treści dokumentów, nazw plików ani tematów maili (ADR-025): log proxy usuwa parametry zapytania, `Referer`, `HX-Current-URL`, cele przekierowań, `Content-Disposition` i `X-CSRF-Token`; log dostępu aplikacji usuwa parametry zapytania; błędy bazy są logowane tylko z typem i SQLSTATE. Żądanie wygląda więc w logu proxy jak `"uri":"/documents?[removed]"`. Jeśli dodajesz własne zbieranie logów (na przykład kolejne proxy przed tym), skonfiguruj to samo; `make smoke` to sprawdza. Przeglądarki zapisują adresy wyszukiwań we własnej historii; to jest poza serwerem.
- `.env` i backupy zawierają sekrety i dane osobowe; ogranicz uprawnienia.
- Regularnie aktualizuj obrazy bazowe (nowe wydanie je przebudowuje).
- Nie dawaj aplikacji dostępu do `docker.sock`; aktualizacje robi się z hosta.
