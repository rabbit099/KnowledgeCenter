# Decyzje, założenia i otwarte pytania

> Dotyczy: v0.0.0 (przed pierwszym wydaniem) · Ostatnia aktualizacja: 2026-11-12

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
Status: przyjęta. Obraz workera jest duży (Tesseract, LibreOffice); obraz app pozostaje mały. Obraz db niesie słownik Hunspell.

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

Każda wersja jest przypięta w `uv.lock`. Kolejne fazy dodają własne wpisy (na przykład `argon2-cffi`, `ldap3`, `pypdfium2`, `python-docx`, `extract-msg`, `charset-normalizer`, `lingua-language-detector`, `python-magic`) w miarę wprowadzania.

## Założenia

- „Na start ok 200” (odpowiedź na pytanie o oczekiwany rozmiar) rozumiemy jako około 200 dokumentów. Cel projektowy to 20 000 dokumentów i 100 użytkowników, dopóki nie zostanie to potwierdzone.
- Wielkość serwera nie jest znana; limity zasobów są konfigurowalne.
- Interfejs tylko po polsku; teksty przechodzą przez katalogi gettext.
- Spodziewane są tylko dokumenty po polsku i angielsku.

## Otwarte pytania

1. Czy Publisher może publikować dokumenty wgrane przez innych? Domyślnie: nie, tylko własne (`documents.publish_any` obejmuje resztę).
2. Retencja: jak długo przechowywać dokumenty zarchiwizowane i wpisy audytowe (RODO)?
3. Certyfikat TLS: wewnętrzne CA czy Let's Encrypt?
4. Miejsce docelowe backupów (NAS, drugi serwer, magazyn obiektowy) i integracja z monitoringiem.
5. Czy Reader ma też widzieć dokumenty zarchiwizowane? Domyślnie: nie.
6. Czy skanowanie antywirusowe (ClamAV) jest wymagane polityką firmy? Domyślnie: opcjonalny profil, wyłączony.
7. Czy „ok 200” to dokumenty czy użytkownicy?
8. Do rozstrzygnięcia w Fazie 2: zdarzenia w obrębie procesu nie są trwałe. Jeśli proces padnie między „tekst wyodrębniony” a „zaindeksowano w wyszukiwarce” (albo handler zgłosi błąd), zdarzenie przepada. Opcje: oprzeć się na `processing_status` i poleceniu ponownego przetwarzania (`baza-wiedzy reprocess`) albo na tabeli outbox. Nic nie budujemy przed Fazą 2.
