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
