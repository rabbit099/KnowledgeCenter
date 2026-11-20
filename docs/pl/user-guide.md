# Wyszukiwanie — przewodnik dla użytkowników i administratora

> Dotyczy: v0.4.1 · Ostatnia aktualizacja: 2026-11-20

Jak znajdować dokumenty, co wyszukiwanie potrafi, a czego nie, kto widzi co i co uruchamia administrator. Szczegóły techniczne (indeksy, ranking, pomiary): [search.md](search.md).

## Dla użytkowników

### Wyszukiwanie

Pole wyszukiwania jest na stronie głównej i pod „Szukaj”. Wpisz co najmniej 2 i najwyżej 200 znaków i naciśnij Enter.

| Wpisz | Znajduje |
|---|---|
| `umowa serwisowa` | dokumenty zawierające **wszystkie** słowa, w dowolnej formie („umowy serwisowej”, „umowie”) |
| `"zasady bezpieczeństwa"` | dokładną frazę: słowa obok siebie w tej kolejności |
| `faktura -korekta` | dokumenty z „faktura” i **bez** „korekta” w całym dokumencie |
| `faktura or rachunek` | dokumenty z jednym z tych słów (`or` po angielsku, małymi literami) |

Wielkość liter nie ma znaczenia. Bardzo częste słowa („i”, „w”, „the”) nie pomagają: są w prawie każdym dokumencie, więc zapytanie złożone tylko z nich znajduje niemal wszystko; dodaj konkretne słowo.

### Odmiana i języki

Polskie i angielskie słowa są znajdowane w każdej formie: `umowa` znajduje „umowy”, „umowie”, „umów”; `invoice` znajduje „invoices”; `Łódź` znajduje „w Łodzi”. Dokument może być w obu językach; każdy fragment jest przeszukiwany w obu. Nazwiska i słowa spoza słownika są znajdowane w formie zapisanej w tekście.

### Numery, kody i adresy

Numery i kody są znajdowane z separatorami i bez:

| Wpisz | Znajduje też |
|---|---|
| `7251801126` albo `725-18-01-126` | NIP zapisany w dowolny z tych sposobów |
| `17/2026` | „UM/17/2026” (fragment między separatorami) |
| `600 100 200`, `600100200`, `+48 600 100 200` | ten sam numer telefonu zapisany w dowolny z tych sposobów |
| `IT002` albo `IT-002`, `EX-PVE0` | kody i nazwy serwerów |
| `example.com`, `ksiegowosc@example.com` | adresy w tej domenie, dokładny adres |
| `192.168.10.25`, `192.168.10` | adres, adresy w tej sieci |

Numery muszą być dokładne: `FV/2026/11/0042` nigdy nie znajdzie `…0043`.

### Wyniki

Wyniki są grupowane po dokumencie, od najlepszego (trafienie w tytule albo temacie maila jest wyżej niż w nazwie pliku albo nadawcy, a to wyżej niż w treści). Każdy wynik pokazuje:
- tytuł (link do strony dokumentu, gdzie można pobrać plik);
- typ, nazwę pliku, datę (mail: data wysłania, pozostałe: data wgrania) oraz, dla użytkowników pracujących z cudzymi dokumentami, autora;
- gdzie jest najlepsze trafienie („strona 7”, „załącznik: umowa.pdf › strona 2”) i fragment z podświetlonymi słowami; „Dopasowanie w tytule, nazwie pliku albo nagłówkach maila”, gdy pasowały tylko one;
- „szkic” albo „zarchiwizowany”, gdy dokument nie jest opublikowany.

Strona podaje, ile dokumentów znaleziono; 20 na stronę, linki do stron na dole, najwyżej 50 stron (aby zobaczyć dalej, doprecyzuj zapytanie).

### Filtry i sortowanie

Pod „Filtry i sortowanie”: typ pliku (PDF, Word, e-mail, tekst), język tekstu, data od/do, „Pokaż zarchiwizowane” (tylko dla osób, które mogą widzieć zarchiwizowane), sortowanie według trafności albo daty (od najnowszych). Filtr autora widzą tylko użytkownicy pracujący z cudzymi dokumentami (Admin i role publikujące albo edytujące cudze dokumenty). Każdy filtr jest częścią adresu strony, więc wyszukiwanie można zapisać w zakładkach albo wysłać koledze (który zobaczy tylko to, do czego ma prawo).

### Czego wyszukiwanie nie potrafi

- **Brak synonimów**: „kopia zapasowa” nie znajdzie „backup”, „umowa” nie znajdzie „kontrakt”. Wpisz drugie słowo albo oba: `"kopia zapasowa" or backup`.
- **Bez polskich liter tylko dokładna forma**: `lodz` znajduje „Łódź”, ale nie „w Łodzi” (`lodzi` znajduje). Wpisz polskie litery, aby znaleźć każdą formę.
- **Brak fragmentów słów i numerów**: `faktu` nie znajdzie „faktura” w treści; `7251` nie znajdzie „7251801126”; `1002` nie znajdzie „600 100 200”. Drobne literówki i niepełne słowa są wybaczane tylko w tytułach, nazwach plików, tematach i nadawcach maili (nigdy w numerach).
- **Zeskanowane strony** są znajdowane dopiero po OCR (Faza 4; strona dokumentu pokazuje wtedy „wymaga OCR”). Dokumenty, których przetwarzanie zakończyło się błędem, i dokumenty bez tekstu są znajdowane tylko po tytule i nazwie pliku.
- **Limity**: od 2 do 200 znaków; 20 wyników na stronę; najwyżej 50 stron; wyszukiwanie trwające dłużej niż 3 sekundy jest pokazywane według daty zamiast trafności z adnotacją („Zapytanie jest bardzo ogólne…”); jeśli i to jest zbyt wolne, strona prosi o zawężenie zapytania.

### Kto widzi co

| Dokument | Znajdują go |
|---|---|
| Opublikowany | wszyscy z uprawnieniem do czytania dokumentów (każda rola domyślna) |
| Szkic | jego autor i posiadacze `documents.read_drafts` (domyślnie Admin); oznaczony „szkic” |
| Zarchiwizowany | jego autor i posiadacze `documents.edit_any` (Admin), tylko z „Pokaż zarchiwizowane”; nigdy Czytelnicy |

Wyszukiwanie stosuje te same zasady co strony dokumentów: dokument, którego nie możesz otworzyć, nigdy nie pojawia się w wynikach, także w ich liczbie.

### Prywatność wyszukiwań

To, co wpisujesz, nie jest zapisywane, nie trafia do żadnego logu serwera ani do dziennika audytu. Serwer zapisuje tylko linię z liczbami (ile wyników, ile to trwało). Przeglądarka zapamiętuje wyszukiwania we własnej historii, jak każdą odwiedzoną stronę.

## Dla administratorów

### Obsługa wyszukiwania

Polecenia w kontenerze aplikacji (`docker compose exec app baza-wiedzy …`):

| Polecenie | Kiedy | Oczekiwany wynik |
|---|---|---|
| `search-check` | po aktualizacji albo gdy dokument nie jest znajdowany | `Search index OK.` (kod 0) albo liczby wersji niezaindeksowanych / różnych / osieroconych (tylko identyfikatory) i podpowiedź (kod 1) |
| `reindex-search --missing` | `search-check` zgłosił różnice | `Indexed N version(s); removed M orphaned; took T s.` |
| `reindex-search --all` | po zmianie słownika, konfiguracji albo reguł identyfikatorów (dziennik zmian o tym mówi) | ta sama linia; około 5 minut na 20 000 dokumentów |
| `reindex-search --document ID` | jeden dokument | ta sama linia |

Nowe dokumenty są indeksowane zaraz po przetworzeniu; publikacja, archiwizacja i przywrócenie nie wymagają reindeksu. Po aktualizacji jeden proces workera indeksuje przy starcie to, czego brakuje (`search: indexed N version(s) missing from the index`); pozostałe procesy pomijają ten krok (`…runs in another worker process; skipped here`).

### Narzędzia lokalne (tylko komputer deweloperski)

- `make relevance DIR=<dokumenty> QUERIES=<plik> AS=<użytkownik> [ONLY=1]` wgrywa własne dokumenty jako szkice do **lokalnego stosu deweloperskiego** i drukuje pierwsze wyniki każdego zapytania do ręcznej oceny. **Wynik zawiera tekst dokumentów**: nigdy go nie commituj, nie załączaj do zgłoszeń i nie wklejaj w publiczne miejsca. Repozytorium jest publiczne: trzymaj dokumenty i plik zapytań poza nim (polecenie odmawia ścieżek wewnątrz). Szczegóły: [search.md](search.md#testy-wzorcowe-golden).
- `make perf N=20000` mierzy wyszukiwanie na syntetycznych dokumentach (bez prawdziwych danych); wyniki w [search.md](search.md#cel-wydajnościowy).

### Użytkownicy i dostęp

- Kto widzi autorów: posiadacze `documents.read_drafts`, `documents.publish_any` albo `documents.edit_any` (ADR-030). Edytor ról przy dwóch ostatnich wymaga też `documents.read_drafts`.
- Konta: `baza-wiedzy users list` i `baza-wiedzy users set-password <login>` (tylko konta lokalne); zobacz [auth-and-permissions.md](auth-and-permissions.md#lokalny-administrator-awaryjny).

### Kontrola prywatności

Wyszukiwane frazy, nazwy plików i tematy maili nigdy nie trafiają do logów, dziennika audytu ani komunikatów błędów (ADR-025). `make smoke` sprawdza to przy każdym wydaniu; na serwerze: wyszukaj unikalne słowo, potem `docker compose logs proxy app worker db | grep -c <słowo>` musi wypisać `0`.
