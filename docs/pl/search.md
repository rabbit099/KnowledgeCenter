# Wyszukiwanie

> Dotyczy: v0.0.0 (przed pierwszym wydaniem) · Ostatnia aktualizacja: 2026-11-12

Wyszukiwanie to pełnotekstowe wyszukiwanie PostgreSQL (FTS), a nie OCR. OCR tylko zamienia zeskanowane strony na tekst; potem każdy dokument jest przeszukiwany tak samo.

## Cele

- Użytkownik wpisuje frazę i dostaje dokumenty (oraz załączniki maili), które ją zawierają.
- Polska fleksja działa: szukanie „faktura” znajduje też „faktury”, „fakturze”, „fakturą”.
- Angielski działa tak samo („invoice” znajduje „invoices”).
- Kody, nazwy serwerów, numery i nazwy własne, których nie ma w żadnym słowniku, też są znajdowane.
- Zapytania wpisane bez polskich znaków działają („zolw” znajduje „żółw”).
- Frazy w cudzysłowie i wykluczenia działają.

## Indeksacja

Każdy wiersz `srch_parts` jest indeksowany jednocześnie w trzech konfiguracjach:

| Kolumna | Konfiguracja | Obsługuje |
|---|---|---|
| `tsv_pl` | `pl`: słownik Hunspell, a dla słów nieznanych `simple` | Polską fleksję |
| `tsv_en` | `english` (wbudowany stemmer) | Angielską fleksję |
| `tsv_simple` | `simple_unaccent`: bez stemmingu, bez znaków diakrytycznych | Kody, nazwy, numery, zapytania bez polskich znaków |

Wykryty język (`srch_parts.language`) ma charakter informacyjny i może służyć jako filtr. Nigdy nie decyduje, których indeksów użyć, dzięki czemu dokumenty dwujęzyczne działają.

Dzielenie na części (chunking) utrzymuje każdy tsvector poniżej limitu 1 MB w PostgreSQL: PDF-y są dzielone na strony, inny tekst na fragmenty do 100 000 znaków.

Moduł `search` jest właścicielem zaindeksowanego tekstu i jego indeksów (tabela `srch_parts`). Tekst dostaje od modułu `ingestion` przez zdarzenie `document.version.extracted` i zastępuje wszystkie części danej wersji w jednej transakcji, więc powtórzenie zdarzenia jest nieszkodliwe. Ponieważ tekst jest przechowywany tutaj, reindeksacja nie wymaga ponownego czytania plików oryginalnych, a wymiana silnika wyszukiwania dotyka wyłącznie tego modułu.

## Przetwarzanie zapytania

Wejściem jest tekst wpisany przez użytkownika. Dla każdej konfiguracji zapytanie jest budowane przez `websearch_to_tsquery`, które rozumie:

- `słowo1 słowo2` — oba słowa
- `"dokładna fraza"` — fraza
- `słowo1 or słowo2` — którekolwiek
- `-słowo` — wykluczenie

Część pasuje, gdy `tsv_pl @@ q_pl OR tsv_en @@ q_en OR tsv_simple @@ q_simple`. Puste zapytanie (same stop words) zwraca pusty wynik z podpowiedzią, a nie błąd.

Metadane (tytuł, nazwa pliku, temat i nadawca maila) są dopasowywane przez tsvector oraz podobieństwo `pg_trgm`, więc części słów i literówki też coś tam znajdą.

## Ranking

- Treść: `ts_rank_cd(..., 32)` dla każdej konfiguracji, wygrywa najlepsza wartość (wynik `simple_unaccent` jest mnożony przez 0,8, żeby dopasowania ze stemmingiem lądowały odrobinę wyżej).
- Dopasowanie w metadanych (tytuł/nazwa pliku) dodaje premię.
- Wyniki są grupowane po dokumencie; dokument reprezentuje jego najlepsza część. Wynik pokazuje też, która część pasowała (numer strony, nazwa załącznika).

## Widoczność (bezpieczeństwo)

Wymuszana w SQL dla każdego zapytania:

- użytkownik musi mieć `documents.read`;
- zwykli użytkownicy: `doc_documents.status = 'published'`, a część należy do `doc_documents.published_version_id`;
- autorzy dodatkowo widzą własne szkice (ostatnia wersja);
- posiadacze `documents.read_drafts` widzą wszystkie szkice;
- dokumenty zarchiwizowane są wykluczone, chyba że filtr wyraźnie o nie prosi i użytkownik może je widzieć.

Stare wersje nie są przeszukiwalne w MVP.

Reguła widoczności należy do modułu `documents`: `documents.services.api` udostępnia fragment SQL, a `search` wstrzykuje go do swoich zapytań (szczegóły projektowe zostaną ustalone w Fazie 3).

## Fragmenty i podświetlanie

`ts_headline` jest kosztowne i nie escapuje HTML, więc:

1. Uruchamiaj je tylko dla wyników na widocznej stronie (domyślnie 20).
2. Jako `StartSel`/`StopSel` użyj znaczników-sentineli (ciągów, które nie występują w zwykłym tekście).
3. Zescapuj cały fragment w Pythonie (HTML).
4. Zamień sentinele na `<mark>` i `</mark>`.

Nigdy nie przekazuj surowego fragmentu do szablonu jako bezpiecznego HTML.

## Filtry i paginacja

Filtry: kategoria, rodzaj dokumentu, język, zakres dat, nadawca maila, „tylko moje dokumenty”. Paginacja: 20 na stronę z przyciskiem „więcej” przez HTMX.

## Przykładowy SQL

```sql
WITH q AS (
  SELECT websearch_to_tsquery('pl', :q)              AS q_pl,
         websearch_to_tsquery('english', :q)         AS q_en,
         websearch_to_tsquery('simple_unaccent', :q) AS q_simple
)
SELECT d.id AS document_id, p.id AS part_id,
       GREATEST(ts_rank_cd(p.tsv_pl, q.q_pl, 32),
                ts_rank_cd(p.tsv_en, q.q_en, 32),
                0.8 * ts_rank_cd(p.tsv_simple, q.q_simple, 32)) AS score
FROM srch_parts p
JOIN doc_versions v  ON v.id = p.version_id
JOIN doc_documents d ON d.id = v.document_id
CROSS JOIN q
WHERE (p.tsv_pl @@ q.q_pl OR p.tsv_en @@ q.q_en OR p.tsv_simple @@ q.q_simple)
  AND ( (d.status = 'published' AND v.id = d.published_version_id)
     OR (d.status = 'draft' AND v.id = d.latest_version_id
         AND (d.owner_id = :user_id OR :can_read_drafts)) )
ORDER BY score DESC
LIMIT :limit;
```

To szkic: prawdziwa implementacja grupuje po dokumencie, dodaje dopasowania w metadanych i filtry.

## Słownik polski (Hunspell) — konfiguracja

1. Obraz `db` kopiuje `pl_pl.dict` i `pl_pl.affix` (UTF-8, nazwy małymi literami) oraz listę stop words `polish.stop` do katalogu `tsearch_data` PostgreSQL.
2. Init SQL:

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

3. Weryfikacja: `SELECT ts_lexize('pl_hunspell', 'faktury');` zwraca formę podstawową.

### Spike (Faza 0) — trzeba zrobić na początku

Słownik Hunspell trzeba sprawdzić, zanim zbuduje się na nim resztę:

- Potwierdź, że PostgreSQL ładuje wybrane pliki słownika i afiksów dla polskiego (formaty plików afiksów bywają różne i niektóre się nie ładują).
- Sprawdź, czy licencja słownika jest akceptowalna do użytku w firmie.
- Uruchom testy wzorcowe z poniższej sekcji.
- Zapisz tutaj wynik (źródło i wersja słownika, wyniki testów, znane braki).

Rozwiązania zapasowe, jeśli Hunspell zawiedzie lub jakość będzie słaba, w tej kolejności:
1. Lematyzacja w Pythonie (Morfeusz 2 lub polski model spaCy) w workerze, z zapisem lematów w dodatkowym tsvectorze w konfiguracji `simple`.
2. Zastąpienie backendu Meilisearchem za interfejsem `SearchBackend`.

Wynik spike'a: _jeszcze niewykonany_.

## Reindeksacja

Kolumny `tsv_*` są generowane z `text_content`. Po zmianie słownika, listy stop words lub konfiguracji uruchom:

```
docker compose exec app baza-wiedzy reindex-search
```

Polecenie przepisuje `text_content` partiami (co przelicza kolumny generowane), a potem wykonuje `REINDEX` na indeksach GIN. Czas trwania dla aktualnej ilości danych zapisz tutaj po pierwszym prawdziwym uruchomieniu.

## Testy wzorcowe (golden)

`tests/search_golden/` zawiera zapytania z oczekiwanymi dokumentami z syntetycznych fixture'ów. Minimalne przypadki:

| Zapytanie | Musi znaleźć dokument zawierający |
|---|---|
| `faktura` | „faktury”, „fakturze”, „fakturą” |
| `umowa` | „umowie”, „umów” |
| `zolw` | „żółw” |
| `invoice` | „invoices” |
| `"zasady bezpieczeństwa"` | dokładną frazę, a nie dwa słowa osobno |
| `faktura -korekta` | dokumenty bez słowa „korekta” |
| `EX-PVE0` | dosłowną nazwę serwera |
| `FV/2026/11/0042` | dosłowny numer dokumentu |
| słowo występujące tylko w załączniku maila | dokument-mail |
| słowo występujące tylko na zeskanowanej stronie | zeskanowany dokument (po OCR) |

## Cel wydajnościowy

p95 poniżej 500 ms na syntetycznym korpusie 20 000 dokumentów. Mierz testem wydajności i zapisuj wyniki tutaj.
