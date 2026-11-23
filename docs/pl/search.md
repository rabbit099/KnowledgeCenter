# Wyszukiwanie

> Dotyczy: v0.4.1 · Ostatnia aktualizacja: 2026-11-23

Wyszukiwanie to pełnotekstowe wyszukiwanie PostgreSQL (FTS), a nie OCR. OCR tylko zamienia zeskanowane strony na tekst; potem każdy dokument jest przeszukiwany tak samo.

## Cele

- Użytkownik wpisuje frazę i dostaje dokumenty (oraz załączniki maili), które ją zawierają.
- Polska odmiana działa: szukanie „faktura” znajduje też „faktury”, „fakturze”, „fakturą”.
- Angielski działa tak samo („invoice” znajduje „invoices”).
- Kody, nazwy serwerów, liczby i nazwiska spoza słowników też są znajdowane, również zapisane z innymi separatorami („725-18-01-126” = „7251801126”).
- Zapytania wpisane bez polskich znaków nadal działają („zolw” znajduje „żółw”).
- Frazy w cudzysłowie i wykluczenia działają.

## Jak wyszukiwać (dla użytkowników)

Przewodnik dla użytkowników i administratora — składnia zapytań, odmiana, numery i kody, wyniki, filtry, ograniczenia (brak synonimów, polskie litery, fragmenty słów, 2–200 znaków, 20 na stronę, 50 stron, 3 s), widoczność, prywatność i polecenia operatora — jest w [user-guide.md](user-guide.md). Ten dokument opisuje, jak wyszukiwanie działa w środku.

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

Każde z trzech zapytań jest łączone przez OR z dwiema przepisanymi formami, w których każdy identyfikator jest sklejony (druga także bez początkowego numeru kierunkowego), zobacz następną sekcję. Część pasuje, gdy `tsv_pl @@ q_pl OR tsv_en @@ q_en OR tsv_simple @@ q_simple`. Wykluczenie usuwa cały dokument, gdy którakolwiek z jego części albo jego metadane zawierają wykluczone słowo w dowolnej konfiguracji (nie tylko pasująca część). Stop words są usuwane przez `pl` i `english`, ale zostają w `simple_unaccent`, więc zapytanie z bardzo częstych słów nadal pasuje (do wielu dokumentów); tylko zapytanie puste we wszystkich trzech konfiguracjach nie zwraca wyników, tylko podpowiedź.

Metadane (tytuł, nazwa pliku, temat maila, nadawca, odbiorcy) są dopasowywane tsvectorami z `srch_documents` oraz, dla zapytań co najmniej 4-znakowych bez cyfr i bez operatorów, podobieństwem słów `pg_trgm` (`SEARCH_TRIGRAM_THRESHOLD`, domyślnie 0,6), więc literówka w tytule nadal go znajduje. Numery nigdy nie pasują przez podobieństwo („FV/2026/11/0042” nie może znaleźć „…0043”).

## Identyfikatory (numery i kody)

Parser tekstu dzieli albo skleja identyfikatory inaczej w dokumencie i w zapytaniu: „UM/17/2026” to jeden token (więc „17/2026” by go nie znalazło), „600 100 200” to trzy tokeny, „IT-002” daje „it” i „-002”, adres e-mail to jeden token (więc jego domena by go nie znalazła). Dlatego `search/identifiers.py` dodaje przy indeksowaniu znormalizowane tokeny bez separatorów (`ident_terms`, we wszystkich trzech tsvectorach) i tak samo przepisuje zapytanie (ADR-026):

| Rozpoznawane | Przykład | Dodatkowe tokeny |
|---|---|---|
| Litery/cyfry połączone `-` albo `/`, z cyfrą | `UM/17/2026`, `725-18-01-126`, `IT-002`, `EX-PVE0` | każdy ciąg członów sklejony i każdy człon: `um172026`, `172026`, `um17`, `2026`, … |
| Grupy cyfr rozdzielone spacjami albo myślnikami, ≥ 6 cyfr, opcjonalnie `+` | `600 100 200`, `+48 600 100 200` | każdy ciąg grup sklejony: `600100200`, `48600100200`, `100200`, … |
| Adresy e-mail | `ksiegowosc@example.com` | część przed @ i jej kawałki, domena i domeny nadrzędne |
| Nazwy hostów | `intranet.example.com` | nazwa i domeny nadrzędne |
| Adresy IPv4 | `192.168.10.25` | adres i jego pierwsze trzy oktety |

Zapytanie dostaje sklejoną formę każdego identyfikatora jako alternatywę (`725-18-01-126` → `7251801126`, `+48 600 100 200` → `48600100200` i `600100200`, `+48501222333` → `48501222333`); słowa zachowują polskie znaki, więc słownik polski nadal działa („umowie 17/2026” znajduje „Umowa UM/17/2026”). Oryginalne zapytanie zawsze zostaje, więc nic, co ono znajduje, nie ginie.

Nie działa (z założenia): fragment jednego członu („0042” w „00042”); prefiksy („7251*”); ciąg niekończący się na separatorze („1002” w „600 100 200”); litery i cyfry rozdzielone tylko spacjami („UM 17 2026”); inne prefiksy krajowe („0048”); IPv6; zakresy i CIDR; literówki w numerach. Koszt: kilka dodatkowych tokenów na identyfikator (ciąg n członów daje do n(n+1)/2, najwyżej 8 członów we wszystkich kombinacjach, najwyżej 5000 tokenów na część); sklejone liczby stojące obok siebie w tekście („2019 2020 2021”) tworzą tokeny, których nikt nie szuka — nieszkodliwe.

## Ranking

Dla każdego dokumentu (ADR-027):
- treść: najlepsza część; `ts_rank_cd(…, 32)` konfiguracji wykrytego języka części (`pl` albo `english`; „nieznany”: lepsza z obu); dopiero gdy daje 0 (identyfikator, słowo wpisane bez polskich znaków, słowo drugiego języka), lepsza z `simple_unaccent` × 0,8 i drugiego języka × 0,9. Ranking czyta tsvector każdej używanej konfiguracji, co jest głównym kosztem szerokiego zapytania, więc ta kolejność oszczędza większość pracy (ADR-028);
- część, której tekst rozpoznał OCR (`srch_parts.ocr_applied`, `search_0003`), liczy się × 0,9 (`OCR_FACTOR`, ADR-034): przy tych samych słowach warstwa tekstu jest wyżej niż tekst z OCR, który może zawierać błędy rozpoznania; trafienie w OCR i tak jest wyżej niż słabsze dopasowanie gdzie indziej;
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

`ts_headline` sam pomija tokeny znaczników, np. `<script>`; pozostały tekst jest escapowany. Nigdy nie przekazuj surowego fragmentu do szablonu jako bezpiecznego HTML. Trafienie w tekście rozpoznanym przez OCR ma obok miejsca etykietę „tekst z OCR”, dla każdego, kto widzi dokument (także Czytelnika). Dokument znaleziony tylko po metadanych pokazuje zamiast fragmentu „Dopasowanie w tytule, nazwie pliku albo nagłówkach maila”.

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
- `test_ocr_golden.py` (Faza 4 etap C): 11 syntetycznych skanów z wadami (skos, szum, rozmycie, mały druk, tabela, obrót, niski kontrast, strona mieszana, skany w załącznikach EML i MSG) przechodzi wgranie, przebieg tekstowy, przebieg OCR, indeksowanie i publikację; Czytelnik musi znaleźć każdy skan po polskich odmianach (protokoły, odbiór, urządzenie, chłodniczy), bez polskich znaków (chlodniczych, zazolc), po formach angielskich (reports, approve) i po frazie; warstwa tekstu jest wyżej niż te same słowa z OCR; wyniki oznaczają tekst z OCR. Numery (telefon, NIP) są sprawdzane tylko na skanach, z których OCR je odczytuje (wszystkie poza skanem z 6 % szumu); zobacz [ingestion-and-ocr.md](ingestion-and-ocr.md#ocr). Wymaga Tesseracta (`make test-db`).
- Tam też: escapowanie HTML z dokumentów, fragmenty i ich lokalizacja, sortowanie, każdy filtr, limity zapytania, limit czasu, brak frazy w logu, `search-check` i `reindex-search`, publikacja działająca bez reindeksu, powtórzone zdarzenia.

**Prawdziwe dokumenty i zapytania (tylko lokalnie).** Repozytorium jest publiczne: dokumenty i zapytania firmy nigdy do niego nie trafiają ani do CI. Oceniaj je lokalnie przez `make relevance` na stosie deweloperskim:

1. Umieść dokumenty w katalogu **poza** repozytorium (na przykład `~/trafnosc/dokumenty/`), a zapytania w pliku poza nim (`~/trafnosc/zapytania.txt`): jedno zapytanie w linii, `#` dla komentarzy, opcjonalnie `zapytanie => fragment oczekiwanego tytułu albo nazwy pliku`.
2. `make up`, zaloguj się raz kontem, które ma być właścicielem szkiców (albo użyj istniejącego konta Publikującego), potem `make relevance DIR=~/trafnosc/dokumenty QUERIES=~/trafnosc/zapytania.txt AS=bw-test ONLY=1`.
3. Pierwsze uruchomienie wgrywa każdy plik jako szkic tego użytkownika i czeka na workera oraz na OCR skanów (linie `processing: {...}`, skany jako `needs_ocr/queued`, `needs_ocr/running`); duże skany (więcej niż `OCR_AUTO_MAX_PAGES` zeskanowanych stron) czekają wstrzymane jak w aplikacji: dodaj `OCR=1`, aby je uruchomić (audyt `document.ocr_request`, `source: relevance`). Kolejne uruchomienia używają tych samych plików (ten sam SHA-256) i tylko wyszukują.
4. Dla każdego zapytania wynik pokazuje liczbę dokumentów, czas i pierwsze `TOP` wyników: wynik, tytuł, nazwę pliku, miejsce trafienia („strona 3”, „załącznik: … › strona 1”, „tytuł/nazwa/nagłówki”; „ (OCR)”, gdy trafienie jest w tekście rozpoznanym przez OCR) i fragment z trafieniami w «»; przy `=>` także OK albo MISSING i podsumowanie na końcu.
5. Oceń wyniki ręcznie; zapisuj tu tylko wnioski bez treści (na przykład „18 z 20 zapytań ma oczekiwany dokument na pierwszym miejscu; problemy: skróty”).

Szkice zostają na stosie deweloperskim, widoczne tylko dla tego użytkownika i posiadaczy `documents.read_drafts`; aby je usunąć, odtwórz bazę deweloperską (`make down`, potem `docker volume rm baza-wiedzy_pgdata baza-wiedzy_storage`, co kasuje wszystkie dane lokalne). Wynik zawiera tekst dokumentów: nigdy go nie commituj, nie wklejaj ani nie załączaj.

## Cel wydajnościowy

p95 poniżej 500 ms na syntetycznym korpusie 20 000 dokumentów (CLAUDE.md 10): **spełniony** — p95 329 ms dla wszystkich typów zapytań, jeden użytkownik. Zmierzono 2026-11-18 przez `make perf` (ręcznie; [deployment-operations.md](deployment-operations.md#polecenia-dla-programisty)): jednorazowy PostgreSQL z ustawieniami produkcyjnymi (`max_connections` 40, `shared_buffers` 256 MB, limit pamięci 2 GB), deterministyczny korpus polsko-angielski (słownictwo Zipfa z prawdziwymi słowami biznesowymi na początku, numery faktur, NIP, telefony, nazwy hostów, 15 % maili, 10 % szkiców), na 14-wątkowej maszynie deweloperskiej pod WSL2 (współdzielonej, więc przebiegi różnią się do ±30 %; sprzęt produkcyjny może być inny). Pełne wyniki z planami zapytań: `perf-results/perf-N.md` danego przebiegu (poza gitem).

| | 20 000 dokumentów | 100 000 dokumentów (informacyjnie) |
|---|---|---|
| Części, tekst | 44 800 części, 28 mln znaków | 225 000 części, 142 mln znaków |
| Pełne indeksowanie, jeden proces (= jeden proces workera) | 304 s, około 3900 dokumentów/min (przebiegi: 3300–4600) | 1441 s (24 min), około 4200 dokumentów/min |
| Rozmiar bazy | 381 MB | 1770 MB |
| `srch_parts` (sterta + TOAST + indeksy) | 268 MB | 1263 MB |
| Kolumny tsvector `tsv_pl` / `tsv_en` / `tsv_simple` | 46 / 47 / 50 MB (sam tekst 28 MB) | 233 / 238 / 253 MB |
| Indeksy GIN sześciu tsvectorów | 91 MB | 358 MB |
| Indeks trigramowy (metadane) | 5,6 MB | 16 MB |
| Wszystkie zapytania, jeden użytkownik: p50 / p95 / maks. | **98 / 329 / 404 ms** | 467 / 2138 / 4681 ms |
| 10 równoczesnych wyszukiwań, ciepłe: p50 / p95, przepustowość | 279 / 1508 ms, 16 wyszukiwań/s | 1254 / 5247 ms, 3,6 wyszukiwania/s |
| 10 równoczesnych, nowe połączenia (ładowanie słownika) | 267 / 1965 ms | 1635 / 5533 ms (część przekroczyła limit czasu) |

Według typu zapytania przy 20 000 dokumentów (Czytelnik, p50 / p95 ms): numer faktury 25 / 30; NIP 26 / 33; telefon 27 / 35; literówka w tytule 15 / 19; dwa słowa angielskie 58 / 63; fraza 68 / 79; rzadkie słowo 68 / 173; sortowanie według daty 103 / 111; dwa słowa 147 / 152; filtr typu i daty 210 / 223; bardzo częste słowo 211 / 229; jedno częste słowo („umowa”, 13 556 dokumentów) 224 / 239; dwa słowa odmienione 240 / 248; wykluczenie „faktura -korekta” 332 / 373. Admin (także szkice) mieści się w 10 % tych wartości.

**Co zajmuje czas.** `EXPLAIN (ANALYZE, BUFFERS)` szerokiego zapytania: dopasowanie przez trzy indeksy GIN 5–20 ms; ranking czyta i rozpakowuje tsvector (średnio około 1 KB, 71 leksemów) dla każdej części i konfiguracji, około 3 µs na część i konfigurację plus odczyt; `ts_headline` dla 20 wyników 30 ms; Python 12 ms. Strojenie w etapie C (ADR-028), z efektem przy 20 000 dokumentów: łączenie przez OR tylko alternatyw identyfikatorów różnych od zapytania (potrajały pracę dopasowania i rankingu: „umowa” 368 → 236 ms), ranking najpierw w konfiguracji języka części, pozostałe tylko gdy ta daje 0, wąskie zmaterializowane CTE zamiast szerokich wierszy w złączeniach (bez plików tymczasowych), wykluczenia jednym przejściem po GIN (`faktura -korekta` 1026 → 332 ms), `work_mem` 16 MB na wyszukiwanie. Ogólne p95: 1032 ms przed, 329 ms po. Wypróbowane i odrzucone: tsvectory bez kompresji (`STORAGE EXTERNAL`, ranking tylko o 15 % szybszy, więcej miejsca).

**Limit równoległości.** Przepustowość zatrzymuje się na około 10–16 wyszukiwaniach/s niezależnie od liczby wątków (1 wątek 4,9/s, 4 wątki 10,8/s przy p95 488 ms, 10 wątków 10,1/s); baza używa 6 z 14 rdzeni. Przyczyna: jedno wyszukiwanie bierze 35 blokad relacji (tabele i każdy indeks rozważany przez planer), PostgreSQL 16 trzyma 16 na sesję w szybkiej ścieżce, reszta idzie przez współdzielony menedżer blokad i do 30 % aktywnych sesji czeka na `LWLock:LockManager`. Dla 100 użytkowników (kilka wyszukiwań jednocześnie) to wystarcza; opcje, gdyby nie wystarczało: mniej relacji na instrukcję (nazwy autorów i daty kopiowane do indeksu zamiast złączeń) albo PostgreSQL 18 z większą liczbą slotów szybkiej ścieżki (ADR, poza Fazą 3).

**100 000 dokumentów.** Szerokie zapytania trwają 1,5–2,4 s, a równoległość spada: baza (1,8 GB) nie mieści się już w 256 MB `shared_buffers` i limicie kontenera 2 GB, więc ranking czyta z dysku. Przed taką wielkością: `POSTGRES_SHARED_BUFFERS` około 1 GB i `DB_MEMORY_LIMIT` około 4 GB, potem ponowny pomiar; kolejnym krokiem byłby limit liczby rankingowanych części. Do tego czasu limit czasu z wynikami według daty utrzymuje stronę responsywną.

**Słownik bez polskich znaków** (oceniony, niewdrożony; ADR-028): drugi słownik Hunspell bez diakrytyków (`pl_unaccent`) sprawiłby, że `lodz` znajdzie „w Łodzi” (8 z 10 sprawdzonych słów znalezionych w innej formie: Gdańsku, źródła, świadczeń, …). Koszt: 24,8 MB więcej na każde połączenie z bazą (dziś 27,3 MB), czyli 2,09 GB na oba słowniki przy 40 połączeniach — więcej niż dzisiejsze `DB_MEMORY_LIMIT` 2 GB; około 230 MB tsvector + 95 MB GIN więcej przy 100 000 dokumentów (czwarta konfiguracja, około +25 % indeksu); oraz nowe fałszywe trafienia: 12 z 14 sprawdzonych par różnych słów (łaski ↔ laska, sądu ↔ sad, żony ↔ zona, kąta ↔ kat, rządu ↔ rzad, więcej ↔ wiec, …); 3715 grup rdzeni słownika (2,4 % z 308 304) zlewa się w jeden klucz. Dzisiejsze zachowanie (dokładna forma bez znaków przez `simple_unaccent`) zostaje.

