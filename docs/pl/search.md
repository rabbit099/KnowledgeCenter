# Wyszukiwanie

> Dotyczy: v0.3.0 · Ostatnia aktualizacja: 2026-11-18

Wyszukiwanie to pełnotekstowe wyszukiwanie PostgreSQL (FTS), a nie OCR. OCR tylko zamienia zeskanowane strony na tekst; potem każdy dokument jest przeszukiwany tak samo.

## Cele

- Użytkownik wpisuje frazę i dostaje dokumenty (oraz załączniki maili), które ją zawierają.
- Polska odmiana działa: szukanie „faktura” znajduje też „faktury”, „fakturze”, „fakturą”.
- Angielski działa tak samo („invoice” znajduje „invoices”).
- Kody, nazwy serwerów, liczby i nazwiska spoza słowników też są znajdowane, również zapisane z innymi separatorami („725-18-01-126” = „7251801126”).
- Zapytania wpisane bez polskich znaków nadal działają („zolw” znajduje „żółw”).
- Frazy w cudzysłowie i wykluczenia działają.

## Jak wyszukiwać (dla użytkowników)

Pole wyszukiwania jest na stronie głównej i pod „Szukaj” (`/search`); wymaga uprawnienia `documents.read` (mają je wszystkie role domyślne).

| Wpisz | Znajduje |
|---|---|
| `umowa serwisowa` | dokumenty zawierające oba słowa, w dowolnej formie („umowy serwisowej”, „umowie”) |
| `"zasady bezpieczeństwa"` | dokładną frazę (słowa obok siebie) |
| `faktura -korekta` | dokumenty z „faktura” i bez „korekta” w całym dokumencie |
| `faktura or rachunek` | dokumenty z jednym z tych słów (`or` po angielsku, małymi literami) |
| `725-18-01-126`, `7251801126` | ten sam NIP zapisany z myślnikami albo bez |
| `603 388 100`, `+48 603 388 100` | ten sam numer telefonu ze spacjami, bez spacji, z +48 albo bez |
| `17/2026` | także „UM/17/2026” (fragment między separatorami) |
| `example.com`, `192.168.10` | adresy e-mail i nazwy hostów w tej domenie, adresy w tej sieci |

Wyniki są grupowane po dokumencie (pokazywany jest najlepszy fragment z zaznaczonymi trafieniami i miejsce, gdzie jest: „strona 7”, „załącznik: umowa.pdf › strona 2”); szkice są oznaczone „szkic” i znajduje je tylko autor oraz posiadacze `documents.read_drafts`; zarchiwizowane są ukryte, chyba że zaznaczono „Pokaż zarchiwizowane” (tylko autorzy i `documents.edit_any`). Filtry: typ pliku, język tekstu, autor (osoba, która wgrała), zakres dat (mail: data wysłania, pozostałe: data wgrania); sortowanie według trafności albo daty; 20 wyników na stronę, najwyżej 50 stron. Zapytanie musi mieć od 2 do 200 znaków. Drobne literówki w tytule, nazwie pliku, temacie albo nadawcy maila są tolerowane (nie w numerach i nie w treści).

Znane ograniczenia (z założenia, nie błędy):
- Bez polskich znaków słowo jest znajdowane tylko w dokładnie tej formie: `lodz` znajduje „Łódź”, ale nie „w Łodzi”; `lodzi` znajduje „w Łodzi”. Wpisane z polskimi znakami znajduje każdą formę (`Łódź` znajduje „w Łodzi”). Powód: polski słownik działa na słowach z diakrytykami; indeks bez znaków nie zna odmiany.
- Nazwiska i słowa spoza słownika są znajdowane tylko w formie z tekstu („Kowalski” nie znajdzie „Kowalskim”).
- Numery: fragment wewnątrz jednego członu („0042” w „00042”), początek numeru („7251*”) i fragment niekończący się na separatorze („3388” w „603 388 100”) nie są znajdowane.
- Zeskanowane strony są znajdowane dopiero po OCR (Faza 4); dokumenty, których przetwarzanie zakończyło się błędem, nie są znajdowane (otwórz je z „Moich dokumentów”).
- Wyszukiwane frazy nie są nigdzie na serwerze zapisywane, audytowane ani logowane (ADR-025); przeglądarka zapamiętuje je w swojej historii.

## Indeksacja

Każdy wiersz `srch_parts` jest indeksowany jednocześnie w trzech konfiguracjach (ADR-003, utrzymane przez ADR-026):

| Kolumna | Konfiguracja | Obsługuje |
|---|---|---|
| `tsv_pl` | `pl`: słownik Hunspell, z przejściem na `simple` dla nieznanych słów | Polska odmiana |
| `tsv_en` | `english` (wbudowany stemmer) | Angielska odmiana |
| `tsv_simple` | `simple_unaccent`: bez stemmingu, bez akcentów | Kody, nazwy, liczby, zapytania bez polskich znaków |

Każda kolumna jest generowana z `text_content` oraz `ident_terms`, czyli znormalizowanych identyfikatorów tekstu (kolejne sekcje). Wykryty język (`srch_parts.language`) służy do rankingu i jako filtr; nigdy nie decyduje, które indeksy są użyte, więc dokumenty mieszane językowo działają.

Podział na kawałki utrzymuje każdy tsvector poniżej limitu 1 MB PostgreSQL: PDF dzielony jest na strony, inny tekst na kawałki najwyżej 100 000 znaków (robi to ingestion).

`srch_documents` ma jeden wiersz na wersję z metadanymi dopasowywanymi osobno: tytuł i temat maila (waga A), nazwa pliku z separatorami zamienionymi na spacje, nadawca i odbiorcy (waga B), w tych samych trzech konfiguracjach, oraz `meta_fold` (małe litery, bez znaków) z indeksem trigramowym do dopasowań przybliżonych.

Moduł `search` jest właścicielem swojego indeksu; tekst należy do `ingestion` (`ing_parts`, ADR-020), metadane do `documents`. Po zdarzeniu `document.version.extracted` (tylko identyfikatory, dostarczane co najmniej raz przez outbox ingestion) `search` czyta części przez `ingestion.services.api.parts_of` i metadane przez `documents.services.api.index_metadata` i zastępuje wszystkie wiersze tej wersji w jednej transakcji pod blokadą doradczą, więc powtórzenie zdarzenia jest nieszkodliwe. Części bez tekstu (zeskanowana strona przed OCR) są pomijane. Start workera indeksuje każdą wyodrębnioną wersję, która nie ma wierszy (nowa instalacja, utracone zdarzenie). Wiersze są per wersja; to, która wersja jest przeszukiwana (opublikowana albo najnowsza szkicu), decyduje `documents.services.api.search_scope`, więc publikacja, cofnięcie publikacji, archiwizacja i przywrócenie działają od razu bez reindeksu, a Faza 5 może dodać wersje bez przebudowy indeksu. Po OCR (Faza 4) ingestion zapisze części ponownie i ponownie zapisze zdarzenie, a search zastąpi wiersze.

## Przetwarzanie zapytania

Wejściem jest tekst użytkownika (2–200 znaków po zwinięciu spacji; zawsze parametr wiązany). Dla każdej konfiguracji zapytanie budowane jest przez `websearch_to_tsquery`, które rozumie:

- `słowo1 słowo2` — oba słowa
- `"dokładna fraza"` — fraza
- `słowo1 or słowo2` — jedno albo drugie
- `-słowo` — wykluczenie

Każde z trzech zapytań jest łączone przez OR z dwiema przepisanymi formami, w których każdy identyfikator jest sklejony (druga także bez początkowego numeru kierunkowego), zobacz następną sekcję. Część pasuje, gdy `tsv_pl @@ q_pl OR tsv_en @@ q_en OR tsv_simple @@ q_simple`. Wykluczenie usuwa cały dokument, gdy którakolwiek z jego części albo jego metadane zawierają wykluczone słowo w dowolnej konfiguracji (nie tylko pasująca część). Zapytanie złożone wyłącznie z bardzo częstych słów (wszystkie usunięte jako stop words) nie zwraca wyników, tylko podpowiedź.

Metadane (tytuł, nazwa pliku, temat maila, nadawca, odbiorcy) są dopasowywane tsvectorami z `srch_documents` oraz, dla zapytań co najmniej 4-znakowych bez cyfr i bez operatorów, podobieństwem słów `pg_trgm` (`SEARCH_TRIGRAM_THRESHOLD`, domyślnie 0,6), więc literówka w tytule nadal go znajduje. Numery nigdy nie pasują przez podobieństwo („FV/2026/11/0042” nie może znaleźć „…0043”).

## Identyfikatory (numery i kody)

Parser tekstu dzieli albo skleja identyfikatory inaczej w dokumencie i w zapytaniu: „UM/17/2026” to jeden token (więc „17/2026” by go nie znalazło), „603 388 100” to trzy tokeny, „IT-002” daje „it” i „-002”, adres e-mail to jeden token (więc jego domena by go nie znalazła). Dlatego `search/identifiers.py` dodaje przy indeksowaniu znormalizowane tokeny bez separatorów (`ident_terms`, we wszystkich trzech tsvectorach) i tak samo przepisuje zapytanie (ADR-026):

| Rozpoznawane | Przykład | Dodatkowe tokeny |
|---|---|---|
| Litery/cyfry połączone `-` albo `/`, z cyfrą | `UM/17/2026`, `725-18-01-126`, `IT-002`, `EX-PVE0` | każdy ciąg członów sklejony i każdy człon: `um172026`, `172026`, `um17`, `2026`, … |
| Grupy cyfr rozdzielone spacjami albo myślnikami, ≥ 6 cyfr, opcjonalnie `+` | `603 388 100`, `+48 603 388 100` | każdy ciąg grup sklejony: `603388100`, `48603388100`, `388100`, … |
| Adresy e-mail | `ksiegowosc@example.com` | część przed @ i jej kawałki, domena i domeny nadrzędne |
| Nazwy hostów | `intranet.example.com` | nazwa i domeny nadrzędne |
| Adresy IPv4 | `192.168.10.25` | adres i jego pierwsze trzy oktety |

Zapytanie dostaje sklejoną formę każdego identyfikatora jako alternatywę (`725-18-01-126` → `7251801126`, `+48 603 388 100` → `48603388100` i `603388100`, `+48501222333` → `48501222333`); słowa zachowują polskie znaki, więc słownik polski nadal działa („umowie 17/2026” znajduje „Umowa UM/17/2026”). Oryginalne zapytanie zawsze zostaje, więc nic, co ono znajduje, nie ginie.

Nie działa (z założenia): fragment jednego członu („0042” w „00042”); prefiksy („7251*”); ciąg niekończący się na separatorze („3388” w „603 388 100”); litery i cyfry rozdzielone tylko spacjami („UM 17 2026”); inne prefiksy krajowe („0048”); IPv6; zakresy i CIDR; literówki w numerach. Koszt: kilka dodatkowych tokenów na identyfikator (ciąg n członów daje do n(n+1)/2, najwyżej 8 członów we wszystkich kombinacjach, najwyżej 5000 tokenów na część); sklejone liczby stojące obok siebie w tekście („2019 2020 2021”) tworzą tokeny, których nikt nie szuka — nieszkodliwe.

## Ranking

Dla każdego dokumentu (ADR-027):
- treść: najlepsza część; `ts_rank_cd(…, 32)` w każdej konfiguracji, wygrywa najlepsza wartość; `simple_unaccent` × 0,8 (dopasowania ze stemmingiem są nieco wyżej); konfiguracja języka innego niż wykryty dla części × 0,9 („nieznany”: obie w pełni);
- plus metadane: `ts_rank(…, 32)` z wagami A (tytuł, temat) = 1,0 i B (nazwa pliku, nadawca, odbiorcy) = 0,4, więc trafienie w tytule jest wyżej niż w treści; dopasowanie przybliżone (trigramy) liczy się jako `0,3 × podobieństwo`, poniżej dopasowania dokładnego;
- remisy: nowszy dokument pierwszy, potem według id (deterministycznie).

Sortowanie według daty: najnowsze pierwsze (mail: data wysłania, pozostałe: data wgrania), trafność jako drugie kryterium. Wynik pokazuje, która część pasowała (numer strony, ścieżka załącznika).

## Widoczność (bezpieczeństwo)

Wymuszana w SQL dla każdego zapytania:

- użytkownik widzi dokładnie te dokumenty, które dopuszcza `visibility_clause` ([auth-and-permissions.md](auth-and-permissions.md#widoczność-dokumentów)): opublikowane z `documents.read`, wszystkie szkice z `documents.read_drafts`, własne dokumenty we wszystkich stanach; szkice są w wynikach oznaczone „szkic”;
- przeszukiwana wersja to opublikowana (`published_version_id`) dla dokumentów opublikowanych i zarchiwizowanych oraz najnowsza dla szkiców (`Document.current_version_id`);
- dokumenty zarchiwizowane są wykluczone, chyba że filtr „pokaż zarchiwizowane” o nie prosi i użytkownik może je widzieć (właściciel, `documents.edit_any`); Czytelnicy nigdy ich nie widzą.

Wyszukiwane frazy nigdy nie są logowane, audytowane ani zapisywane (ADR-025).

Stare wersje nie są przeszukiwalne w MVP.

Reguła widoczności należy do modułu `documents`: `documents.services.api` udostępnia fragment SQL, a `search` wstrzykuje go do swoich zapytań (szczegóły projektowe zostaną ustalone w Fazie 3).

## Fragmenty i podświetlanie

`ts_headline` jest kosztowne i nie escapuje HTML, dlatego:

1. Działa tylko dla najlepszej części każdego wyniku na widocznej stronie (najwyżej 20), w konfiguracji, która pasowała (`MaxWords=35, MinWords=15, MaxFragments=2`).
2. Podświetlenie używa dwóch znaków z obszaru prywatnego (U+E000, U+E001) jako `StartSel`/`StopSel`; indekser usuwa je z każdego tekstu, więc dokument nie może podrobić podświetlenia.
3. Cały fragment jest escapowany w Pythonie (`snippets.to_html`).
4. Dopiero potem wartownicy zamieniani są na `<mark>` i `</mark>`.

`ts_headline` sam pomija tokeny znaczników, np. `<script>`; pozostały tekst jest escapowany. Nigdy nie przekazuj surowego fragmentu do szablonu jako bezpiecznego HTML. Dokument znaleziony tylko po metadanych pokazuje zamiast fragmentu „Dopasowanie w tytule, nazwie pliku albo nagłówkach maila”.

## Filtry i paginacja

Filtry (wszystkie w adresie URL zwykłego formularza GET, stosowane w SQL `search_scope`): typ pliku (PDF, Word = DOC/DOCX, e-mail = EML/MSG, tekst = TXT), język pasującej części (`pl`, `en`, `unknown`; dopasowania tylko po metadanych są pomijane, gdy wybrano język), autor (właściciel dokumentu), data od/do (dni lokalne, Europe/Warsaw; mail: data wysłania, pozostałe: data wgrania), „Pokaż zarchiwizowane” (tylko dla użytkowników, którzy mogą widzieć zarchiwizowane). Kategorie w Fazie 5. Sortowanie: trafność albo data. Strony: `SEARCH_PAGE_SIZE` (20) wyników, numerowane linki, najwyżej `SEARCH_MAX_PAGES` (50); liczba znalezionych dokumentów jest dokładna (`count(*) OVER ()`, bez dodatkowego kosztu, bo ranking i tak czyta każde dopasowanie).

## Limity i prywatność

- Każde wyszukiwanie działa w jednej transakcji z `SET LOCAL statement_timeout = SEARCH_TIMEOUT_MS` (3000 ms). Jeśli wyszukiwanie z rankingiem go przekroczy (bardzo ogólne zapytanie), uruchamiane jest ponownie bez rankingu, od najnowszych, i strona to mówi; jeśli i to przekroczy limit, użytkownik dostaje prośbę o zawężenie zapytania.
- Zapytanie jest zawsze parametrem wiązanym; długość 2–200 znaków (`SEARCH_MIN_QUERY_CHARS`, `SEARCH_MAX_QUERY_CHARS`).
- Wyszukiwane frazy nigdy nie trafiają do logu, audytu ani komunikatu błędu (ADR-025). Każde wyszukiwanie zapisuje jedną linię z samymi licznikami: `search: results=12 page=1 sort=relevance filters=kind,date fallback=no duration_ms=48` (nazwy użytych filtrów, nigdy ich wartości); podwójne przekroczenie limitu zapisuje `search: time limit exceeded twice`.

## Przykładowy SQL

Uproszczony kształt instrukcji budowanej przez `search/postgres.py` (prawdziwa dodaje alternatywy identyfikatorów, część metadanych, wykluczenia i filtry):

```sql
WITH q AS (
  SELECT websearch_to_tsquery('pl', :q)              || websearch_to_tsquery('pl', :alt)              AS pl,
         websearch_to_tsquery('english', :q)         || websearch_to_tsquery('english', :alt)         AS en,
         websearch_to_tsquery('simple_unaccent', :q) || websearch_to_tsquery('simple_unaccent', :alt) AS simple
), content AS (
  SELECT p.document_id, p.id AS part_id,
         GREATEST(ts_rank_cd(p.tsv_pl, q.pl, 32), ts_rank_cd(p.tsv_en, q.en, 32),
                  0.8 * ts_rank_cd(p.tsv_simple, q.simple, 32)) AS score
  FROM srch_parts p
  JOIN (/* documents.services.api.search_scope(user, filters) */) s ON s.version_id = p.version_id
  CROSS JOIN q
  WHERE p.tsv_pl @@ q.pl OR p.tsv_en @@ q.en OR p.tsv_simple @@ q.simple
), best AS (
  SELECT DISTINCT ON (document_id) * FROM content ORDER BY document_id, score DESC, part_id
)
SELECT document_id, part_id, score, count(*) OVER () AS total
FROM best ORDER BY score DESC, document_id LIMIT 20 OFFSET 0;
```

## Słownik polski (Hunspell) — konfiguracja

1. Obraz `db` (`docker/db/Dockerfile`) instaluje przypięty pakiet Debiana `hunspell-pl`, sprawdza sumy SHA-256 jego plików, konwertuje je do UTF-8 i kopiuje `pl_pl.dict`, `pl_pl.affix`, własną listę `polish.stop` oraz plik licencji do katalogu `tsearch_data` PostgreSQL. Obraz nie tworzy obiektów wyszukiwania; jego jedyny skrypt init tworzy przy pustym wolumenie zwykłego użytkownika aplikacji i bazę (`docker/db/initdb/10-app-role.sh`).
2. Obiekty w bazie tworzą migracje Alembic (rozszerzenia w gałęzi `core`, słownik i konfiguracje w gałęzi `search`). Działają jako użytkownik aplikacji, który nie potrzebuje uprawnień superużytkownika (zob. [deployment-operations.md](deployment-operations.md#konfiguracja)). Obiekty:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TEXT SEARCH DICTIONARY pl_hunspell (
  TEMPLATE = ispell, DictFile = pl_pl, AffFile = pl_pl, StopWords = polish);

CREATE TEXT SEARCH CONFIGURATION pl (COPY = simple);
ALTER TEXT SEARCH CONFIGURATION pl
  ALTER MAPPING FOR asciiword, word, numword, asciihword, hword, numhword,
                    hword_asciipart, hword_part, hword_numpart
  WITH pl_hunspell, simple;

CREATE TEXT SEARCH CONFIGURATION simple_unaccent (COPY = simple);
ALTER TEXT SEARCH CONFIGURATION simple_unaccent
  ALTER MAPPING FOR asciiword, word, numword, asciihword, hword, numhword,
                    hword_asciipart, hword_part, hword_numpart
  WITH unaccent, simple;
```

3. Weryfikacja: `SELECT ts_lexize('pl_hunspell', 'faktury');` zwraca `{faktura}`. `docker/db/spike.sql` to skrypt diagnostyczny: tworzy tymczasowe kopie tych obiektów w transakcji wycofywanej na końcu i wypisuje przykładowe wyniki.

### Spike (Faza 0) — trzeba zrobić na początku

Słownik Hunspell trzeba sprawdzić, zanim zbuduje się na nim resztę:

- Potwierdź, że PostgreSQL ładuje wybrane pliki słownika i afiksów dla polskiego (formaty plików afiksów bywają różne i niektóre się nie ładują).
- Sprawdź, czy licencja słownika jest akceptowalna do użytku w firmie.
- Uruchom testy wzorcowe z poniższej sekcji.
- Zapisz tutaj wynik (źródło i wersja słownika, wyniki testów, znane braki).

Rozwiązania zapasowe, jeśli Hunspell zawiedzie lub jakość będzie słaba, w tej kolejności:
1. Lematyzacja w Pythonie (Morfeusz 2 lub polski model spaCy) w workerze, z zapisem lematów w dodatkowym tsvectorze w konfiguracji `simple`.
2. Zastąpienie backendu Meilisearchem za interfejsem `SearchBackend`.

Wynik spike'a (2026-11-12): **Hunspell wystarcza; rozwiązanie zapasowe nie jest potrzebne.**

- Słownik: pakiet Debiana `hunspell-pl` `1:25.2.3-1` (Debian 13), zbudowany z `libreoffice-dictionaries` 25.2.3; źródło SJP.PL (Marek Futrega). Licencja źródłowa: „GPL or LGPL or MPL or Apache-2.0 or CC-SA-1.0”; projekt używa go na licencji **Apache-2.0** (ADR-016). SHA-256 oryginalnych plików: `pl_PL.aff` `7c37b9bd…54b38e`, `pl_PL.dic` `215fd73a…110aad` (pełne wartości w ADR-016 i w `docker/db/dictionary/LICENSE-pl_pl-dictionary.txt`). Około 308 000 haseł.
- PostgreSQL 16.15 ładuje pliki po konwersji do UTF-8 (pakiet zawiera ISO-8859-2). Znaleziona pułapka: dopóki pakiet jest zainstalowany, debianowy `postgresql-common` tworzy w `tsearch_data` dowiązania `pl_pl.*`; Dockerfile usuwa pakiet i te dowiązania, zanim skopiuje zwykłe pliki.
- `ts_lexize('pl_hunspell', …)`: faktura, faktury, fakturze, fakturą → `{faktura}`; umowa → `{umowa}`; umowie → `{umowie,umowa}`; umów → `{umów,umówić,umowa}`; żółw → `{żółw}`; pracownika, pracownikom → `{pracownik}`; zawarł → `{zawrzeć}`; stop word `nie` → `{}`; nieznane słowo → NULL (przechodzi do `simple`).
- Testy wzorcowe (`make test-db`): 17 zaliczonych, 2 pominięte (przypadki załącznika e-maila i OCR czekają na Fazę 2). Działają jako zwykły użytkownik (nie superużytkownik) będący właścicielem bazy, co dowodzi, że migracje nie będą potrzebować uprawnień superużytkownika.
- Koszt na połączenie z bazą (zmierzony przez `pg_backend_memory_contexts` i pamięć cgroup kontenera): każdy proces backendu ładuje słownik osobno przy pierwszym użyciu. **Około 27 MB na połączenie** (kontekst „TS dictionary”); 16 równoczesnych połączeń dodało około 480 MiB pamięci kontenera. Pierwsze użycie: około 0,3 s na jednym połączeniu, 0,56–0,80 s, gdy 16 połączeń ładuje słownik jednocześnie. Kolejne wywołania trwają poniżej 1 ms.
- Znane braki: niektóre słowa dostają dwa lematy (`umowie` → `umowie`, `umowa`; `zaświadczenie` → także `zaświadczyć`), co może dodać kilka luźno powiązanych trafień; `zawarta` zostaje w formach imiesłowu (`zawarta`, `zawarty`) zamiast `zawrzeć`. Słowa spoza słownika (nazwiska, kody, nowe terminy) indeksuje `simple` bez zmian.
- Konsekwencje: pule połączeń z bazą mają być małe i stałe (na przykład app 5 + nadmiar 5, worker dwa połączenia na proces); licz około 30 MiB pamięci bazy na połączenie; żadnego wzorca „połączenie na żądanie”. Rozszerzenie `shared_ispell`, które współdzieli słownik między backendami, nie jest potrzebne przy tej skali i jest spoza stacku.

## Reindeksacja

Kolumny `tsv_*` są generowane z `text_content` i `ident_terms`. Polecenia (`docker compose exec app baza-wiedzy …`):

| Polecenie | Kiedy | Oczekiwany wynik |
|---|---|---|
| `search-check` | w każdej chwili; po aktualizacji; gdy dokument nie jest znajdowany | `Extracted versions: N`, liczby `not indexed`, `different number of parts`, `indexed but no longer extracted` (tylko identyfikatory), potem `Search index OK.` (kod 0) albo podpowiedź reindeksu (kod 1) |
| `reindex-search --missing` | `search-check` zgłosił różnice | `Indexed N version(s); removed M orphaned; took T s.` |
| `reindex-search --all` | po zmianie słownika, listy stop words, konfiguracji albo reguł identyfikatorów | ta sama linia; przepisuje każdy wiersz (co przelicza kolumny generowane), potem `REINDEX` obu tabel |
| `reindex-search --document ID` / `--version ID` | jeden dokument | ta sama linia |

Start workera sam wykonuje część `--missing` (linia logu `search: indexed N version(s) missing from the index`). Po pierwszym prawdziwym uruchomieniu zapisz tu czas `--all` dla bieżącej ilości danych.

## Testy wzorcowe (golden)

`tests/search_golden/` działa w `make test-db` (jednorazowy PostgreSQL z `docker/db` na tmpfs, także w CI); `make test` je pomija.

- `test_golden.py`: słownik i trzy konfiguracje na krótkich tekstach (formy podstawowe, stop words, frazy, wykluczenie, kody).
- `test_corpus.py`: syntetyczny korpus polsko-angielski `tests/fixtures/search_corpus.py` (26 dokumentów, 48 zapytań; te same pliki `make probki` zapisuje do `wyszukiwanie/` z `OCZEKIWANE-wyszukiwanie.txt`). Każdy plik przechodzi wgranie, ekstrakcję, indeksowanie po zdarzeniu i publikację; każde zapytanie idzie przez `/search` jako Czytelnik, autor albo Admin i jest porównywane z „musi znaleźć”, „nie może znaleźć” i „pierwszy wynik”. Pokryte: odmiana (umowa/umowy/umowie), polskie znaki (Łódź/lodz), nazwiska, frazy, wykluczenie w innej części, zapytania dwujęzyczne, angielska liczba mnoga, słowa tylko w załączniku maila, temacie albo nadawcy, częściowo zeskanowany PDF, słowo tylko w nazwie pliku, literówka w tytule, szkice i zarchiwizowane według roli, identyfikatory (NIP, numery umów, telefony, nazwy hostów, kody, adresy e-mail, domeny, adresy IP). To, że `lodz` nie znajduje „w Łodzi”, jest udokumentowanym przypadkiem (oczekiwanie „nie może znaleźć”), a nie błędem.
- Tam też: escapowanie HTML z dokumentów, fragmenty i ich lokalizacja, sortowanie, każdy filtr, limity zapytania, limit czasu, brak frazy w logu, `search-check` i `reindex-search`, publikacja działająca bez reindeksu, powtórzone zdarzenia.

Gdy dotrą prawdziwe (niepoufne) dokumenty i typowe zapytania właściciela, są testowane lokalnie (nigdy nie trafiają do repozytorium): wgraj je do instalacji testowej i zapisz wyniki w tej sekcji.

## Cel wydajnościowy

p95 poniżej 500 ms na syntetycznym korpusie 20 000 dokumentów (CLAUDE.md 10). Mierzone w Fazie 3, etap C, generatorem 20 000 i 100 000 dokumentów (`make perf`, ręcznie); wyniki i plany zapytań zostaną zapisane tutaj.

