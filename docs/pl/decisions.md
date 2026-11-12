# Decyzje, założenia i otwarte pytania

> Dotyczy: v0.1.0 · Ostatnia aktualizacja: 2026-11-12

Rejestr decyzji (ADR). Nowe decyzje się dopisuje; starej decyzji nigdy się nie przepisuje, tylko zastępuje ją nowym wpisem.

## Decyzje

### ADR-001 — Interfejs renderowany po stronie serwera: FastAPI + Jinja2 + HTMX
Status: przyjęta. Małe narzędzie wewnętrzne utrzymywane przez jedną osobę. Interfejs renderowany na serwerze nie wymaga osobnego buildu frontendu i trzyma cały kod w Pythonie. Konsekwencja: bardzo interaktywne funkcje (na przykład edycja w przeglądarce) byłyby kosztowne; są poza zakresem.

### ADR-002 — Wyszukiwanie pełnotekstowe PostgreSQL jako silnik wyszukiwania
Status: przyjęta. Jedna baza na dane, uprawnienia i indeks; reguły widoczności stosowane w tym samym SQL; wystarcza na dziesiątki tysięcy dokumentów. Konsekwencja: polska fleksja zależy od słownika Hunspell (ADR-003). Wyszukiwanie siedzi za interfejsem `SearchBackend`, więc można je zastąpić Meilisearchem.

### ADR-003 — Trzy konfiguracje wyszukiwania tekstowego dla każdej części tekstu
Status: przyjęta. `pl` (Hunspell + zapasowo simple), `english`, `simple_unaccent`; każda część jest indeksowana we wszystkich, a wykrywanie języka ma charakter informacyjny. Dokumenty dwujęzyczne oraz kody i nazwy działają. Koszt: trzy kolumny tsvector i indeksy (akceptowalne w tej skali). Podlega spike'owi z Fazy 0; rozwiązania zapasowe są w [search.md](search.md).

### ADR-004 — Dramatiq + Redis do zadań w tle
Status: przyjęta. OCR i parsowanie są obciążające dla CPU i synchroniczne; procesowe workery Dramatiq do tego pasują. Redis trzyma tylko kolejkę.

### ADR-005 — pypdfium2 zamiast PyMuPDF do tekstu z PDF
Status: przyjęta. PyMuPDF ma licencję AGPL; pypdfium2 ma licencję permisywną i wystarcza do wyciągania tekstu. Wracać do tematu tylko za zgodą prawną.

### ADR-006 — OCRmyPDF + Tesseract z `pol+eng`
Status: przyjęta. Tworzy PDF z warstwą tekstu i pomija strony, które już mają tekst. OCR jest w MVP.

### ADR-007 — Sesje po stronie serwera w ciasteczkach, a nie JWT
Status: przyjęta. Sesje można unieważnić i są proste przy stronach renderowanych na serwerze oraz HTMX.

### ADR-008 — Active Directory przez LDAPS z użytkownikami tworzonymi just-in-time
Status: przyjęta. Grupy mapują się na role w panelu admina. Lokalny administrator awaryjny zostaje. SSO/Kerberos to możliwy późniejszy krok.

### ADR-009 — Jawny krok migracji w skrypcie aktualizacji
Status: przyjęta. Migracje uruchamiają się w jednorazowym kontenerze z `update.sh`, a nie niejawnie przy starcie aplikacji. Nieudana migracja to jasny, pojedynczy punkt awarii. Aplikacja nie zgłasza gotowości, jeśli wersja schematu jest za stara.

### ADR-010 — Trzy obrazy: app, worker, db
Status: zastąpiona przez ADR-017. Obraz workera jest duży (Tesseract, LibreOffice); obraz app pozostaje mały. Obraz db niesie słownik Hunspell.

### ADR-011 — Synchroniczny SQLAlchemy 2 z psycopg 3
Status: przyjęta. Maksymalnie ~100 użytkowników; prostszy kod i jeden styl dostępu do danych wspólny dla `app` i `worker`.

### ADR-012 — Aktualizacje wykonuje się z hosta, nie z interfejsu WWW
Status: przyjęta. Oddanie aplikacji webowej kontroli nad Dockerem byłoby poważnym ryzykiem bezpieczeństwa. Interfejs może w przyszłości pokazywać „dostępna nowa wersja”.

### ADR-013 — Modularny monolit: stały rdzeń plus moduły `documents`, `ingestion`, `search`
Status: przyjęta. Jedna baza kodu i jeden obraz na rolę (`app`, `worker`), bez osobnych usług. Rdzeń (konfiguracja, baza, auth, uprawnienia, audyt, szkielet panelu admina, rejestr modułów, magistrala zdarzeń) jest zawsze włączony; `documents`, `ingestion` i `search` to moduły w `src/baza_wiedzy/modules/<nazwa>/`, włączane przez `ENABLED_MODULES`. Każdy moduł ma manifest (`module.py`) z nazwą, wersją, `requires`, uprawnieniami, pozycjami menu, routerami, szablonami, gałęzią migracji, schematem ustawień, zadaniami w tle, poleceniami CLI i subskrypcjami zdarzeń. Rejestr sprawdza zależności przy starcie. Wynikające z tego decyzje:
- Moduły rozmawiają przez `services/api.py` i synchroniczne zdarzenia w obrębie procesu (`document.version.uploaded`, `document.version.extracted`, `document.published`), nigdy przez swoje wnętrza.
- Każdy moduł jest właścicielem swoich tabel z prefiksem `doc_`, `ing_`, `srch_`; rdzeń nie ma prefiksu. `search` jest właścicielem zaindeksowanego tekstu (`srch_parts`), więc wymiana silnika dotyka tylko tego modułu; `ingestion` publikuje wyciągnięty tekst jako zdarzenie, a `search` go indeksuje; status przetwarzania, metadane wersji i metadane maili zostają w `documents`, a `ingestion` aktualizuje je przez `documents.services.api`.
- Kierunek dla widoczności: `documents.services.api` udostępnia fragment SQL z regułą widoczności, a `search` wstrzykuje go do swoich zapytań, więc widoczność pozostaje w SQL (szczegóły w Fazie 3).
- Jedna gałąź Alembica na moduł plus `core`; moduł wymagający innego używa `depends_on`.
- Punkty rozszerzeń: rejestr ekstraktorów, `SearchBackend`, dostawcy logowania, magazyn plików. Ładowane są tylko moduły z repozytorium; bez dynamicznych wtyczek.

Odrzucone alternatywy: zwykły układ pakietów bez manifestów (granice się rozmywają i nic nie sprawdza, co jest włączone) oraz osobne usługi (utrzymanie kilku wdrożeń jest zbyt ciężkie dla jednej osoby). Konsekwencje: niewielka ilość kodu szkieletu do utrzymania; zdarzenia utracone przy awarii muszą dać się odtworzyć (handlery są idempotentne, a ponowne przetworzenie publikuje je od nowa); wyłączenie modułu nie usuwa jego danych.

### ADR-014 — Granice modułów egzekwuje import-linter w CI
Status: przyjęta. Granica istniejąca tylko w dokumentacji prędzej czy później zostanie przekroczona, zwłaszcza w długim projekcie budowanym z asystentem AI. import-linter sprawdza graf importów w `make lint` i w CI kontraktami `forbidden`: rdzeń nie importuje modułów, a moduł może importować inny moduł tylko przez jego `services/api.py` (i tylko wymagany). `allow_indirect_imports` jest włączone, bo publiczne API z natury importuje wnętrze swojego modułu. Alternatywy: `banned-api` w ruff (nie wyrazi „wszystko poza `services/api.py`”) oraz same przeglądy (niewiarygodne). Konsekwencje: dodanie modułu wymaga dopisania kontraktu w `pyproject.toml`; kontraktu nie osłabia się, żeby zmiana się skompilowała — zmienia się projekt.

### ADR-015 — Dodatkowe zależności (wpis zbiorczy)
Status: przyjęta. Nic spoza stacku z `CLAUDE.md`, sekcja 3, bez wpisu tutaj.
- Faza 0, etap A: `pydantic` i `pydantic-settings` (typowana konfiguracja ze zmiennych środowiskowych, wymagana przez sekcję 6); tylko do rozwoju: `import-linter` (ADR-014), `ruff`, `mypy`, `pytest`, `pytest-cov`, `pre-commit` (wiersz Quality ze stacku).
- Faza 0, etap B (dodawane przy pierwszym użyciu): `fastapi` i `uvicorn` (framework WWW i jego serwer), `jinja2` (szablony, wymagane dla stron renderowanych na serwerze), `python-multipart` (parsowanie formularzy i uploadu plików w FastAPI), `babel` (katalogi gettext: ekstrakcja, kompilacja, tłumaczenie na polski), `sqlalchemy`, `alembic`, `psycopg` (dostęp do bazy i migracje ze stacku), `dramatiq` i `redis` (kolejka i klient Redis, używany także przez sprawdzanie `/ready`).

- Faza 0, etap B2: `psycopg[binary]` zamiast samego `psycopg` (dodatek `binary` zawiera libpq, więc obrazy nie potrzebują kompilatora ani `libpq-dev`). Narzędzie testowe, nie zależność Pythona: obraz `ghcr.io/astral-sh/uv:0.13.7-python3.12-trixie-slim` uruchamia `make test-db` (ten sam `uv` co w rozwoju, przypięty tag).
- Faza 0, etap B3: `ocrmypdf` jako opcjonalny dodatek `worker` (instalowany tylko w obrazie workera; silnik OCR ze stacku, używany jako biblioteka Pythona). Obrazy bazowe, przypięte do konkretnych tagów: `python:3.12.16-slim-trixie`, `ghcr.io/astral-sh/uv:0.13.7-python3.12-trixie-slim` (etap budowania), `caddy:2.10.2-alpine`, `redis:8.2.3-alpine`, `clamav/clamav:1.4.3`.
- Faza 0, etap B4: `fastapi` i `uvicorn` (framework WWW i serwer ASGI), `jinja2` (szablony renderowane na serwerze), `babel` (ekstrakcja gettext i polski katalog, kompilowany w pamięci przy starcie), `sqlalchemy` (silnik i pula połączeń, używane teraz przez `/ready`, później przez modele), `redis` (klient dla sprawdzenia `/ready`; używa go też Dramatiq). Tylko do rozwoju: `httpx` (wymagany przez `TestClient` FastAPI). Zasób front-endowy, nie pakiet Pythona: HTMX 2.0.11 (0BSD), pobrany z rejestru npm ze sprawdzeniem opublikowanej sumy integralności, dołączony w `src/baza_wiedzy/core/web/static/vendor/`. `python-multipart` odkładamy do czasu uploadu (Faza 2).
- Zasada rozwiązywania wersji: `[tool.uv] exclude-newer` w `pyproject.toml` sprawia, że `uv lock` wybiera tylko wydania opublikowane przed ustaloną datą, ustawioną co najmniej dwa tygodnie wstecz, więc świeżo opublikowane (być może przejęte albo zepsute) wydanie nigdy nie wejdzie automatycznie. Przesunięcie daty to świadomy krok aktualizacji.
- Wyjątek dla poprawek bezpieczeństwa: gdy zależność publikuje poprawkę bezpieczeństwa, bierzemy ją bez czekania dwóch tygodni, albo przesuwając `exclude-newer` (jeśli pozostałe aktualizacje, które to przyniesie, są akceptowalne), albo odblokowując tylko tę paczkę przez `[tool.uv] exclude-newer-package = { <nazwa> = "<data>" }` i `uv lock --upgrade-package <nazwa>`. Wpis w dzienniku zmian wskazuje biuletyn bezpieczeństwa. Wyjątek dla pojedynczej paczki usuwamy przy najbliższym przeglądzie miesięcznym.
- Miesięczny przegląd zależności (pierwszy tydzień roboczy miesiąca): przesuń `exclude-newer` na 14 dni przed przeglądem, `uv lock --upgrade`, przeczytaj `git diff uv.lock`, uruchom `make lint test test-db smoke`, sprawdź tagi obrazów bazowych (Python, Caddy, Redis, PostgreSQL, uv, ClamAV) i wersję debianowego `hunspell-pl`, zatwierdź jako `chore(deps): monthly review RRRR-MM` z wpisem w dzienniku zmian. Zmieniony `hunspell-pl` oznacza nowe sumy kontrolne w ADR-016 i `reindex-search`.
- Faza 0, etap B5: `alembic` (migracje ze stacku; jedna gałąź na moduł).
- Faza 0, etap B6: `dramatiq[redis]` (kolejka zadań ze stacku; dodatek `redis` dociąga już wymieniony klient Redis).
- Faza 0, etap B7 (CI, nie pakiety Pythona): akcje GitHub `actions/checkout` v7.0.1, `astral-sh/setup-uv` v10.3.2, `docker/setup-buildx-action` v4.4.1, `docker/login-action` v4.7.0, `docker/build-push-action` v7.4.0, każda przypięta do SHA commita (starsze niż `exclude-newer`); workflowy sprawdzone przez `actionlint`, skrypty powłoki przez `shellcheck` (oba uruchamiane z obrazów Dockera, nie instalowane w projekcie).
- Dependabot (`.github/dependabot.yml`), zgodnie z powyższymi zasadami: `github-actions` (aktualizuje przypięte SHA commitów razem z komentarzem wersji), `docker` (obrazy bazowe w `docker/*/Dockerfile`) i `docker-compose` (`redis`, `clamav`, tag i digest), wszystkie co miesiąc, pogrupowane, z 14-dniowym `cooldown`, który odpowiada `exclude-newer`. Dla Pythona (`uv`) rutynowe pull requesty z wersjami są wyłączone (`open-pull-requests-limit: 0`): `exclude-newer` i tak by je zablokował, a wersje zmieniamy w przeglądzie miesięcznym; aktualizacje bezpieczeństwa Dependabota (włącz „Dependabot alerts” i „Dependabot security updates” w ustawieniach repozytorium) nadal otwierają pull requesty, obsługiwane według wyjątku dla poprawek bezpieczeństwa (`exclude-newer-package`). Każdy pull request Dependabota przechodzi pełne CI; żaden nie jest scalany bez tego. Przegląd miesięczny zaczyna się od scalenia albo zamknięcia otwartych pull requestów Dependabota. Dependabot nie śledzi debianowego pakietu `hunspell-pl`; przegląd sprawdza go ręcznie.

Każda wersja jest przypięta w `uv.lock`. Kolejne fazy dodają własne wpisy (na przykład `argon2-cffi`, `ldap3`, `pypdfium2`, `python-docx`, `extract-msg`, `charset-normalizer`, `lingua-language-detector`, `python-magic`) w miarę wprowadzania.

### ADR-016 — Polski słownik Hunspell z debianowego `hunspell-pl`, na licencji Apache-2.0
Status: przyjęta. Konfiguracja wyszukiwania `pl` używa słownika Hunspell SJP.PL z pakietu Debiana 13 `hunspell-pl` w wersji `1:25.2.3-1` (z `libreoffice-dictionaries` 25.2.3). Dlaczego: pakiet pochodzi z podpisanego repozytorium, ma stałą wersję i jest już zbudowany dla dystrybucji obrazu bazowego; pobieranie ZIP-a ze strony dodałoby drugą ścieżkę zaufania. Licencja: źródło oferuje „GPL or LGPL or MPL or Apache-2.0 or CC-SA-1.0”; wybieramy permisywną **Apache-2.0**. Obrazy w GHCR są prywatne; tekst licencji i informacja o autorstwie i tak są dołączone do plików (`docker/db/dictionary/LICENSE-pl_pl-dictionary.txt`, kopiowany w obrazie obok `pl_pl.dict`). Integralność: budowanie kończy się błędem, jeśli oryginalne pliki nie mają sum
`pl_PL.aff` SHA-256 `7c37b9bde78054e43365b488a13859094c88bc66664b5b7a7bb073626454b38e` i
`pl_PL.dic` SHA-256 `215fd73aa47b11e7fdd2e4d655e9fe37be4acdae16ff833badcfdfce79110aad`.
Modyfikacje: ISO-8859-2 → UTF-8, `SET UTF-8`, zmiana nazw na `pl_pl.affix` / `pl_pl.dict`. Listę stop words `polish.stop` napisał projekt. Alternatywy: pobranie z sjp.pl z przypiętą sumą kontrolną (odrzucone, patrz wyżej); lematyzacja w Pythonie lub Meilisearch (rozwiązania zapasowe, niepotrzebne: zob. wynik spike'a w `search.md`). Konsekwencje: zmiana wersji pakietu oznacza nowe sumy tutaj, nowy obraz `db`, wpis w dzienniku zmian i `baza-wiedzy reindex-search`. Każde połączenie z bazą trzyma około 27 MB na słownik, więc pule połączeń są małe i stałe.

### ADR-017 — Cztery obrazy: app, worker, db, proxy (zastępuje ADR-010)
Status: przyjęta. Proxy potrzebuje własnego obrazu: oficjalnego obrazu Caddy nie da się uruchomić jako zwykły użytkownik z zapisywalnym magazynem certyfikatów (`/data` należy do roota), a jego binarka ma capability pliku, której `cap_drop: ALL` nie pozwala uruchomić. `docker/caddy/Dockerfile` dodaje użytkownika `caddy`, nadaje mu `/data` i `/config`, usuwa capability pliku i nasłuchuje na 8080/8443. Budowanie go na serwerze (pierwsze podejście w Fazie 0 B3) wymagałoby dostępu do Docker Huba i psuło procedurę offline, dlatego workflow wydania publikuje go jak pozostałe: `baza-wiedzy-{app,worker,db,proxy}:X.Y.Z` w GHCR, `linux/amd64`, z etykietami wersji i SHA commita. Obrazy zewnętrzne, których nie przebudowujemy (`redis`, opcjonalny `clamav`), są przypięte tagiem i digestem w `docker-compose.yml`. Konsekwencje: serwer nie potrzebuje narzędzi do budowania ani kodu źródłowego; pakiet offline (`scripts/offline_bundle.sh`) powstaje z `docker-compose.yml` i zawiera wszystkie sześć obrazów; każda aktualizacja obrazu bazowego Caddy przechodzi przez wydanie.

## Założenia

- „Na start ok 200” (odpowiedź na pytanie o oczekiwany rozmiar) rozumiemy jako około 200 dokumentów. Cel projektowy to 20 000 dokumentów i 100 użytkowników, dopóki nie zostanie to potwierdzone.
- Wielkość serwera nie jest znana; limity zasobów są konfigurowalne.
- Interfejs tylko po polsku; teksty przechodzą przez katalogi gettext.
- Spodziewane są tylko dokumenty po polsku i angielsku.

## Otwarte pytania

1. ~~Czy Publisher może publikować dokumenty wgrane przez innych?~~ Rozstrzygnięte 2026-11-12: nie. Rola Publisher publikuje tylko własne dokumenty; `documents.publish_any` nie należy do roli Publisher (admin może je nadać przez własną rolę).
2. Retencja: jak długo przechowywać dokumenty zarchiwizowane i wpisy audytowe (RODO)?
3. Certyfikat TLS: wewnętrzne CA czy Let's Encrypt? Założenie robocze (2026-11-12): wewnętrzne CA firmy (`TLS_MODE=files`, albo `internal` do testów); Let's Encrypt (`acme`) pozostaje obsługiwanym wariantem. Do potwierdzenia z administratorem domeny.
4. Miejsce docelowe backupów (NAS, drugi serwer, magazyn obiektowy) i integracja z monitoringiem.
5. ~~Czy Reader ma też widzieć dokumenty zarchiwizowane?~~ Rozstrzygnięte 2026-11-12: nie. Reader widzi tylko dokumenty opublikowane.
6. Czy skanowanie antywirusowe (ClamAV) jest wymagane polityką firmy? Domyślnie: opcjonalny profil, wyłączony.
7. Czy „ok 200” to dokumenty czy użytkownicy?
8. Do rozstrzygnięcia w Fazie 2: zdarzenia w obrębie procesu nie są trwałe. Jeśli proces padnie między „tekst wyodrębniony” a „zaindeksowano w wyszukiwarce” (albo handler zgłosi błąd), zdarzenie przepada. Opcje: oprzeć się na `processing_status` i poleceniu ponownego przetwarzania (`baza-wiedzy reprocess`) albo na tabeli outbox. Nic nie budujemy przed Fazą 2.
