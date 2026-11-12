# Wdrożenie i utrzymanie

> Dotyczy: v0.0.0 (przed pierwszym wydaniem) · Ostatnia aktualizacja: 2026-11-12

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
├── docker/caddy/        # Dockerfile i Caddyfile proxy (budowane na serwerze)
├── scripts/             # update.sh, rollback.sh, backup.sh, restore.sh
├── secrets/tls/         # cert.pem i key.pem, tylko przy TLS_MODE=files
├── logs/                # update.log, backup.log
└── backups/             # tworzone przez backup.sh
```

Dane są w nazwanych wolumenach Dockera projektu Compose `baza-wiedzy`: `pgdata` (baza), `storage` (pliki wgrane i pochodne, zamontowany w `/data/storage` w `app` i `worker`), `redis-data` (kolejka), `caddy-data` i `caddy-config` (certyfikaty, w tym wewnętrzne CA Caddy), `clamav-db` (tylko z profilem `antivirus`). Lista: `docker volume ls --filter name=baza-wiedzy`. `docker compose down` je zachowuje; `docker compose down -v` **usuwa wszystkie dane**.

## Konfiguracja

Wszystkie ustawienia to zmienne środowiskowe w `.env`; `.env.example` w repozytorium opisuje każdą zmienną i jest źródłem prawdy. Kluczowe zmienne:

| Zmienna | Znaczenie |
|---|---|
| `APP_NAME` | Wyświetlana nazwa (domyślnie „Baza Wiedzy”) |
| `APP_VERSION` | Tag obrazu do uruchomienia, na przykład `1.0.0` |
| `ENABLED_MODULES` | Opcjonalne moduły do uruchomienia, oddzielone przecinkami (domyślnie `documents,ingestion,search`); rdzeń jest zawsze włączony |
| `PUBLIC_HOSTNAME` | Nazwa DNS używana przez Caddy |
| `TLS_MODE` | `internal`, `files` lub `acme` |
| `SECRET_KEY` | Klucz podpisujący sesje/CSRF |
| `IMAGE_PREFIX` | Rejestr i prefiks nazw obrazów, na przykład `ghcr.io/<właściciel>/baza-wiedzy` |
| `ACME_EMAIL`, `MAX_REQUEST_MB` | Kontakt dla Let's Encrypt (`TLS_MODE=acme`); największe ciało żądania przyjmowane przez proxy |
| `POSTGRES_PASSWORD` | Hasło superużytkownika PostgreSQL; używane tylko wewnątrz kontenera `db` |
| `APP_DB_USER` / `APP_DB_PASSWORD` / `APP_DB_NAME` | Zwykły użytkownik (nie superużytkownik), właściciel bazy aplikacji; używają go aplikacja i migracje. `DATABASE_URL` jest z nich składany w `docker-compose.yml` |
| `POSTGRES_MAX_CONNECTIONS`, `POSTGRES_SHARED_BUFFERS` | Jawny limit połączeń i bufory współdzielone (zob. niżej) |
| `DB_MEMORY_LIMIT`, `APP_MEMORY_LIMIT`, `WORKER_MEMORY_LIMIT`, `REDIS_MEMORY_LIMIT`, `CLAMAV_MEMORY_LIMIT` | Limity pamięci kontenerów |
| `REDIS_PASSWORD` | Hasło wymagane przez Redis (`requirepass`); tylko znaki bezpieczne w URL. `REDIS_URL` jest z niego składany w `docker-compose.yml` |
| `MAX_UPLOAD_MB` i inne limity | Zobacz [ingestion-and-ocr.md](ingestion-and-ocr.md) |
| `LDAP_*` | Zobacz [auth-and-permissions.md](auth-and-permissions.md) |

`ENABLED_MODULES` musi mieć tę samą wartość dla `app` i `worker` (oba czytają ten sam `.env`). Przy starcie aplikacja sprawdza zależności modułów i odmawia uruchomienia z komunikatem w rodzaju `module 'search' requires 'documents', which is not enabled`. `baza-wiedzy migrate` stosuje wyłącznie gałęzie migracji włączonych modułów; po późniejszym włączeniu modułu uruchom `migrate` ponownie. Wyłączenie modułu nie usuwa jego tabel ani danych.

Użytkownik bazy i uprawnienia: aplikacja i `baza-wiedzy migrate` łączą się jako jeden użytkownik bazy, który **nie** jest superużytkownikiem, ale **jest właścicielem bazy aplikacji**. To wystarcza do wszystkiego, co robią migracje: `CREATE EXTENSION pg_trgm` i `CREATE EXTENSION unaccent` (oba rozszerzenia są „zaufane” od PostgreSQL 13, więc właściciel bazy może je utworzyć; pliki rozszerzeń są już w obrazie `db`) oraz `CREATE TEXT SEARCH DICTIONARY` / `CREATE TEXT SEARCH CONFIGURATION` (wymagają `CREATE` na schemacie `public`, które właściciel bazy ma od PostgreSQL 15). Jeśli bazę zakłada ktoś inny, nadaj: `GRANT CREATE ON DATABASE <baza> TO <użytkownik>; GRANT CREATE ON SCHEMA public TO <użytkownik>;`. `make test-db` tworzy obiekty wyszukiwania właśnie jako taki zwykły użytkownik, więc regresja wyjdzie w testach. Przy pierwszym starcie z pustym wolumenem `pgdata` obraz `db` tworzy tego użytkownika i bazę z `APP_DB_USER`, `APP_DB_PASSWORD` i `APP_DB_NAME` (`docker/db/initdb/10-app-role.sh`). Późniejsza zmiana tych zmiennych **nie** zmienia istniejącej bazy; hasło zmień przez `ALTER ROLE` jako `postgres` i jednocześnie zaktualizuj `.env`.

### Połączenia z bazą i pamięć

Każde połączenie z PostgreSQL ładuje polski słownik przy pierwszym użyciu i trzyma go w pamięci: około 27–30 MB na połączenie (zmierzone w spike'u, zob. [search.md](search.md)). Dlatego limit połączeń jest ustawiony jawnie, a limit pamięci `db` z niego wynika:

- Budżet połączeń: app 5 + 5 nadmiarowych na proces aplikacji, worker 1 na proces workera, plus kilka na migracje, backupy i ręczny `psql`. Przy 2 procesach app i 2 procesach workera: 2 × 10 + 2 × 1 + 6 = 28, więc domyślne `POSTGRES_MAX_CONNECTIONS=40` zostawia zapas.
- Pamięć: `DB_MEMORY_LIMIT` ≥ `POSTGRES_SHARED_BUFFERS` + `POSTGRES_MAX_CONNECTIONS` × 35 MB + 300 MB. Domyślnie: 256 MB + 40 × 35 MB + 300 MB ≈ 1,95 GB → `DB_MEMORY_LIMIT=2g`.
- Jeśli zwiększasz `POSTGRES_MAX_CONNECTIONS`, zwiększ `DB_MEMORY_LIMIT` o 35 MB na każde dodatkowe połączenie. Inaczej jądro może zabić PostgreSQL (`docker compose ps` pokazuje restartujący się `db`; `docker inspect baza-wiedzy-db-1 --format '{{.State.OOMKilled}}'` wypisuje `true`).
- Sprawdzenie aktualnych wartości: `docker compose exec db psql -U postgres -c 'SHOW max_connections' -c 'SELECT count(*) FROM pg_stat_activity'`.

## Pierwsza instalacja

1. Skopiuj `docker-compose.yml`, `.env.example`, `docker/caddy/` i `scripts/` do `/srv/baza-wiedzy/`; zmień nazwę `.env.example` na `.env` i wypełnij; `chmod 600 .env`.
2. Zaloguj się do rejestru: `docker login ghcr.io` tokenem z prawem odczytu pakietów.
3. Ustaw `APP_VERSION` i `IMAGE_PREFIX`, potem uruchom: `docker compose pull --ignore-buildable && docker compose build proxy && docker compose up -d`. Obraz proxy jest budowany na serwerze z `docker/caddy/` (kilka sekund; potrzebuje obrazu bazowego `caddy` z Docker Huba).
4. Uruchom migracje: `docker compose run --rm app baza-wiedzy migrate`.
5. Utwórz pierwszego admina: `docker compose exec app baza-wiedzy create-admin <username>`.
6. Sprawdź, czy `https://<PUBLIC_HOSTNAME>/ready` zwraca OK.
7. Zaloguj się jako admin, skonfiguruj mapowanie grup AD, przetestuj logowanie z AD.
8. Zaplanuj backupy (patrz niżej).

## Wydania

- Wersjonowanie: SemVer. Tag Gita `vX.Y.Z` na `main` uruchamia workflow wydania.
- Workflow buduje i wypycha trzy obrazy: `baza-wiedzy-app`, `baza-wiedzy-worker`, `baza-wiedzy-db`, otagowane `X.Y.Z` (a `latest` tylko dla wygody w testach, nigdy w produkcji).
- Przed otagowaniem: przenieś `[Unreleased]` w obu changelogach pod nowy numer wersji i datę.

## Aktualizacja

```
cd /srv/baza-wiedzy
./scripts/update.sh 1.2.0
```

Co robi `update.sh`:
1. Kontrole wstępne: Docker działa, jest wolne miejsce na dysku, nowe obrazy istnieją w rejestrze, `.env` zawiera wszystkie zmienne z nowego `.env.example` (w przeciwnym razie zatrzymuje się i wypisuje brakujące).
2. Uruchamia `backup.sh` (baza + pliki).
3. Pobiera nowe obrazy.
4. Zatrzymuje `worker`, żeby podczas migracji nie szły żadne zadania.
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

Na maszynie z internetem: `docker pull` trzech obrazów, `docker save` do pliku, skopiuj na serwer, `docker load`, a potem uruchom `update.sh` z `SKIP_PULL=1`.

### Po zmianie konfiguracji wyszukiwania

Uruchom `docker compose exec app baza-wiedzy reindex-search` (zobacz [search.md](search.md)).

Dotyczy to każdej zmiany polskiego słownika, listy stop words lub konfiguracji wyszukiwania tekstowego, także nowego obrazu `db` z inną wersją `hunspell-pl` albo `polish.stop` (dziennik zmian wtedy o tym informuje). Istniejące wartości `tsv_*` zostały wyliczone starym słownikiem i nie będą pasować do zapytań przetworzonych nowym, dopóki nie przebudujesz indeksu. Restart `db` zrywa też wszystkie połączenia, więc każde połączenie załaduje nowy słownik przy pierwszym użyciu.

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
| `/ready` | Baza osiągalna, Redis osiągalny, świeży heartbeat workera, wersja schematu równa wersji aplikacji |

Zabbix: sprawdzenie HTTP na `/ready` (oczekiwane 200) oraz trigger na czas odpowiedzi. Endpoint z metrykami i długość kolejki to opcjonalne dodatki na później.

Logi to JSON na stdout. Compose ustawia rotację logów Dockera (`max-size 10m`, `max-file 5`). Czytaj je przez `docker compose logs -f app worker`.

## Polecenia dla programisty

Wszystko idzie przez `Makefile` (z Git Basha na Windows lub z powłoki Linuksa). Python 3.12 i środowisko wirtualne zarządza `uv`; uruchom raz `uv sync`.

| Polecenie | Co robi |
|---|---|
| `make help` | Wypisuje wszystkie polecenia |
| `make lint` | `ruff`, `mypy` i kontrola granic modułów (`lint-imports`) |
| `make fmt` | Formatuje i naprawia automatycznie przez `ruff` |
| `make test` | Uruchamia testy `pytest` (testy integracyjne i wzorcowe wyszukiwania są pomijane) |
| `make test-db` | Uruchamia testy integracyjne i wzorcowe wyszukiwania w Dockerze: jednorazowy PostgreSQL z `docker/db` na tmpfs i jednorazowy kontener testowy w prywatnej sieci Compose (`docker-compose.test.yml`); żaden port nie jest publikowany. Oczekiwany koniec: `17 passed, 2 skipped` (Faza 0). Pierwsze uruchomienie pobiera pakiety Pythona do wolumenu Dockera |
| `make docs-check` | Sprawdza spójność dokumentacji angielskiej i polskiej |
| `make messages` | Wyciąga teksty interfejsu do `messages.pot` i aktualizuje polski katalog `messages.po`; potem uzupełnij nowe pozycje `msgstr` |
| `make up` / `make down` / `make logs` / `make build` | Uruchamia, zatrzymuje, pokazuje logi i buduje stos Dockera (wymaga Dockera) |
| `make migrate` / `make revision m="tekst"` | Stosuje lub tworzy migracje bazy (wymaga Dockera) |
| `make shell` | Powłoka w kontenerze `app` (wymaga Dockera) |
| `make seed-samples` | Zarezerwowane na ładowanie syntetycznych dokumentów przykładowych (jeszcze niedostępne) |

Jeśli `make` jest niedostępny, uruchom te same narzędzia przez `uv run`, na przykład `uv run pytest`.

Lokalny stos: raz `cp .env.example .env` (wartości domyślne wystarczą do rozwoju), potem `make build` i `make up`. `make up` buduje obrazy lokalnie (`docker-compose.dev.yml`), montuje `src/` w `app` i `worker` i przeładowuje serwer WWW po zmianie. Otwórz `https://localhost`: certyfikat pochodzi z wewnętrznego CA Caddy, więc **przeglądarka pokaże ostrzeżenie o certyfikacie**; zaakceptuj je dla `localhost` (albo zaimportuj certyfikat główny: `docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt .`). Z wiersza poleceń użyj `curl -k https://localhost/`. `http://localhost` odpowiada `301` na `https://localhost/`. Na maszynie z małą ilością pamięci (na przykład WSL2, które domyślnie dostaje połowę RAM-u hosta) obniż limity w deweloperskim `.env`: `POSTGRES_MAX_CONNECTIONS=20`, `DB_MEMORY_LIMIT=1g`, `WORKER_MEMORY_LIMIT=1536m`, `APP_MEMORY_LIMIT=512m`; pamięć dostępną dla Dockera sprawdzisz przez `docker info --format '{{.MemTotal}}'`. Wartości domyślne są dobrane pod serwer. Stan Fazy 0: dopóki nie są gotowe etapy B4 (aplikacja WWW) i B6 (actory workera), `app` i `worker` kończą się przy starcie (w dev poddają się po 3 próbach, `restart: on-failure:3`, zamiast restartować się w nieskończoność), a proxy odpowiada `502`; `db` i `redis` osiągają stan `healthy`.

## Rozwiązywanie problemów

| Objaw | Prawdopodobna przyczyna | Co sprawdzić |
|---|---|---|
| Wgrane pliki zostają w `pending` | Worker lub Redis nie działa | `docker compose ps`, `docker compose logs worker redis` |
| `failed` z timeoutem na skanie | Bardzo duży lub słabej jakości skan | Zwiększ `OCR_TIMEOUT_SECONDS`, zeskanuj ponownie, przetwórz ponownie |
| Logowanie AD nie działa u nikogo | Certyfikat LDAPS, konto bind, DNS | `docker compose logs app`, sprawdź `LDAP_CA_FILE`, przetestuj `ldapsearch` |
| Odmienione słowa nie są znajdowane | Słownik niezaładowany lub indeks nieprzebudowany | `SELECT ts_lexize('pl_hunspell','faktury');`, uruchom `reindex-search` |
| `/ready` nie działa po aktualizacji | Migracja nie została zastosowana | `docker compose run --rm app baza-wiedzy migrate`, potem sprawdź `update.log` |
| Pełny dysk | Backupy lub pliki pochodne | Sprawdź `backups/`, ustawienia retencji |
| `could not open dictionary file ".../pl_pl.dict"` | Baza działa na zwykłym obrazie `postgres` zamiast obrazu `db` projektu | `docker compose images db`; przebuduj lub pobierz obraz `db` |
| Aplikacja nie startuje: `module ... requires ...` | `ENABLED_MODULES` zawiera moduł bez modułu, od którego zależy | Dodaj wymagany moduł do `ENABLED_MODULES` w `.env` albo usuń moduł zależny |

## Uwagi bezpieczeństwa dla operatorów

- Wystawione są tylko porty 80/443. Baza i Redis nie są osiągalne z sieci; Redis wymaga też hasła (`REDIS_PASSWORD`).
- Kontenery działają bez roota: `app` i `worker` jako uid 10001, `proxy` jako uid 10002 (nasłuch 8080/8443 wewnątrz, bez capabilities), `db` i `redis` przechodzą na własnych użytkowników po starcie. `app`, `worker` i `proxy` odrzucają wszystkie capabilities Linuksa; wszystkie usługi mają `no-new-privileges`.
- Logi Dockera są rotowane: najwyżej 5 plików × 10 MB na kontener.
- `.env` i backupy zawierają sekrety i dane osobowe; ogranicz uprawnienia.
- Regularnie aktualizuj obrazy bazowe (nowe wydanie je przebudowuje).
- Nie dawaj aplikacji dostępu do `docker.sock`; aktualizacje robi się z hosta.
