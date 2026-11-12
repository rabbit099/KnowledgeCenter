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
├── scripts/             # update.sh, rollback.sh, backup.sh, restore.sh
├── data/                # wolumeny: storage/, postgres/, caddy/
└── backups/             # tworzone przez backup.sh
```

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
| `DATABASE_URL` / `POSTGRES_PASSWORD` | Dostęp do bazy |
| `REDIS_URL` | Kolejka |
| `MAX_UPLOAD_MB` i inne limity | Zobacz [ingestion-and-ocr.md](ingestion-and-ocr.md) |
| `LDAP_*` | Zobacz [auth-and-permissions.md](auth-and-permissions.md) |

`ENABLED_MODULES` musi mieć tę samą wartość dla `app` i `worker` (oba czytają ten sam `.env`). Przy starcie aplikacja sprawdza zależności modułów i odmawia uruchomienia z komunikatem w rodzaju `module 'search' requires 'documents', which is not enabled`. `baza-wiedzy migrate` stosuje wyłącznie gałęzie migracji włączonych modułów; po późniejszym włączeniu modułu uruchom `migrate` ponownie. Wyłączenie modułu nie usuwa jego tabel ani danych.

## Pierwsza instalacja

1. Skopiuj `docker-compose.yml`, `.env.example` i `scripts/` do `/srv/baza-wiedzy/`; zmień nazwę `.env.example` na `.env` i wypełnij; `chmod 600 .env`.
2. Zaloguj się do rejestru: `docker login ghcr.io` tokenem z prawem odczytu pakietów.
3. Ustaw `APP_VERSION` i uruchom: `docker compose pull && docker compose up -d`.
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
9. Dopisuje wynik do `data/update.log`.

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

## Backup

`scripts/backup.sh` tworzy `/srv/baza-wiedzy/backups/RRRR-MM-DD_GGMMSS/` zawierający:
- `db.dump` — `pg_dump -Fc`
- `storage.tar` — pliki oryginalne i pochodne
- `env.backup` — kopia `.env` (zawiera sekrety; zabezpiecz miejsce backupu)
- `SHA256SUMS`

Domyślna retencja: 7 dziennych, 4 tygodniowe, 6 miesięcznych (`BACKUP_KEEP_*`). Kopiowanie backupów poza serwer (NAS, inny serwer, magazyn obiektowy) to hak: ustaw `BACKUP_REMOTE_CMD` na polecenie, które dostaje ścieżkę katalogu backupu. Miejsce docelowe zostanie ustalone później; do tego czasu backupy leżą tylko na tym samym serwerze i to **nie** jest wystarczający backup.

Harmonogram (cron, codziennie w nocy):

```
15 2 * * * /srv/baza-wiedzy/scripts/backup.sh >> /srv/baza-wiedzy/data/backup.log 2>&1
```

Redis przechowuje tylko kolejkę zadań; nie wymaga backupu (niedokończone zadania są kolejkowane ponownie przez `baza-wiedzy reprocess --pending`).

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
| `make test` | Uruchamia testy `pytest` |
| `make docs-check` | Sprawdza spójność dokumentacji angielskiej i polskiej |
| `make up` / `make down` / `make logs` / `make build` | Uruchamia, zatrzymuje, pokazuje logi i buduje stos Dockera (wymaga Dockera) |
| `make migrate` / `make revision m="tekst"` | Stosuje lub tworzy migracje bazy (wymaga Dockera) |
| `make shell` | Powłoka w kontenerze `app` (wymaga Dockera) |
| `make seed-samples` | Zarezerwowane na ładowanie syntetycznych dokumentów przykładowych (jeszcze niedostępne) |

Jeśli `make` jest niedostępny, uruchom te same narzędzia przez `uv run`, na przykład `uv run pytest`.

## Rozwiązywanie problemów

| Objaw | Prawdopodobna przyczyna | Co sprawdzić |
|---|---|---|
| Wgrane pliki zostają w `pending` | Worker lub Redis nie działa | `docker compose ps`, `docker compose logs worker redis` |
| `failed` z timeoutem na skanie | Bardzo duży lub słabej jakości skan | Zwiększ `OCR_TIMEOUT_SECONDS`, zeskanuj ponownie, przetwórz ponownie |
| Logowanie AD nie działa u nikogo | Certyfikat LDAPS, konto bind, DNS | `docker compose logs app`, sprawdź `LDAP_CA_FILE`, przetestuj `ldapsearch` |
| Odmienione słowa nie są znajdowane | Słownik niezaładowany lub indeks nieprzebudowany | `SELECT ts_lexize('pl_hunspell','faktury');`, uruchom `reindex-search` |
| `/ready` nie działa po aktualizacji | Migracja nie została zastosowana | `docker compose run --rm app baza-wiedzy migrate`, potem sprawdź `update.log` |
| Pełny dysk | Backupy lub pliki pochodne | Sprawdź `backups/`, ustawienia retencji |
| Aplikacja nie startuje: `module ... requires ...` | `ENABLED_MODULES` zawiera moduł bez modułu, od którego zależy | Dodaj wymagany moduł do `ENABLED_MODULES` w `.env` albo usuń moduł zależny |

## Uwagi bezpieczeństwa dla operatorów

- Wystawione są tylko porty 80/443. Baza i Redis nie są osiągalne z sieci.
- `.env` i backupy zawierają sekrety i dane osobowe; ogranicz uprawnienia.
- Regularnie aktualizuj obrazy bazowe (nowe wydanie je przebudowuje).
- Nie dawaj aplikacji dostępu do `docker.sock`; aktualizacje robi się z hosta.
