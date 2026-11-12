# Baza Wiedzy — instrukcja krok po kroku

Dla Jakuba. Prowadzi od pustego folderu do działającej aplikacji na serwerze. Kroki 1–5 robisz raz na początku, krok 6 powtarzasz dla każdej fazy, kroki 7–10 dotyczą serwera.

Zawartość paczki:

```
CLAUDE.md                       instrukcja dla Claude Code (po angielsku)
docs/en/ i docs/pl/             dokumentacja (po 9 plików w każdym języku)
INSTRUKCJA_KROK_PO_KROKU.md     ten plik
PIERWSZY_PROMPT.md              pierwszy prompt dla Claude Code
```

Pliki `INSTRUKCJA_KROK_PO_KROKU.md` i `PIERWSZY_PROMPT.md` są dla Ciebie. Możesz je zostawić w głównym katalogu repozytorium (nie wkładaj ich do `docs/en/` ani `docs/pl/`, bo reguła parzystości plików w `docs-check` by to wychwyciła).

---

## Krok 1. Przygotuj stanowisko pracy

- [ ] Git zainstalowany, skonfigurowane `user.name` i `user.email`.
- [ ] Claude Code zainstalowany według aktualnej oficjalnej dokumentacji i zalogowany.
- [ ] Docker z Compose na komputerze, na którym będziesz rozwijać (Docker Desktop lub podobne rozwiązanie). Sprawdź: `docker version` i `docker compose version`.
- [ ] Edytor kodu (VS Code lub inny) do przeglądania zmian.

Jeśli pracujesz na komputerze z procesorem ARM (np. Mac z Apple Silicon), a serwer jest x86_64: lokalnie wszystko działa na ARM, a obrazy produkcyjne zbuduje GitHub Actions dla architektury serwera. Sprawdź architekturę serwera poleceniem `uname -m` (`x86_64` = amd64) i powiedz o tym Claude Code w pierwszym prompcie (jest na to miejsce).

## Krok 2. Załóż repozytorium na GitHubie

1. Utwórz **prywatne** repozytorium `baza-wiedzy` (konto firmowe lub organizacja, jeśli macie).
2. Nie dodawaj README ani `.gitignore` z poziomu GitHuba, repozytorium ma być puste.
3. Ustawienia → Branches: zabezpiecz gałąź `main` (wymagaj przejścia CI przed scaleniem — reguła zadziała, gdy CI będzie już istniało, więc możesz ją włączyć po Fazie 0).
4. Sprawdź w ustawieniach organizacji, że Actions i Packages (GHCR) są włączone.

## Krok 3. Przygotuj katalog projektu

```
mkdir baza-wiedzy && cd baza-wiedzy
git init -b main
git remote add origin git@github.com:<twoje-konto>/baza-wiedzy.git
```

Rozpakuj paczkę do tego katalogu tak, żeby `CLAUDE.md` i `docs/` leżały w jego głównym folderze. Potem:

```
git add .
git commit -m "docs: initial CLAUDE.md and bilingual documentation"
git push -u origin main
```

## Krok 4. Zbierz informacje, które przydadzą się w Fazie 1 i przy wdrożeniu

Nie musisz mieć ich od razu, ale spisz je w jednym miejscu (menedżer haseł, nie repozytorium):

- [ ] Adres kontrolera domeny i port LDAPS (zwykle 636).
- [ ] Certyfikat firmowego CA (plik `.crt` / `.pem`), żeby aplikacja ufała certyfikatowi kontrolera.
- [ ] Konto serwisowe w AD tylko do odczytu (np. `svc-bazawiedzy`) i jego hasło.
- [ ] Base DN (np. `DC=firma,DC=local`).
- [ ] Trzy grupy w AD (nazwy przykładowe): `BazaWiedzy-Admins`, `BazaWiedzy-Publishers`, `BazaWiedzy-Readers`. Dodaj do nich odpowiednie osoby.
- [ ] Nazwa DNS aplikacji (np. `baza.firma.local`).
- [ ] Decyzja: certyfikat z wewnętrznego CA czy Let's Encrypt.
- [ ] Architektura serwera (`uname -m`).

## Krok 5. Uruchom Claude Code z pierwszym promptem

1. W terminalu wejdź do katalogu `baza-wiedzy` i uruchom Claude Code.
2. Otwórz `PIERWSZY_PROMPT.md`, uzupełnij pola w nawiasach kwadratowych, wklej całość.
3. Claude Code najpierw przedstawi plan i pytania. **Przeczytaj plan**, odpowiedz na pytania, dopiero potem zatwierdź implementację.
4. Po skończeniu Fazy 0 dostaniesz raport. Sprawdź listę kontrolną z kroku 6.

## Krok 6. Pętla pracy dla każdej fazy

Fazy są w `docs/pl/README.md` (sekcja „Plan prac”): od 0 do 7.

Dla każdej fazy:

1. **Nowa sesja.** Zacznij czystą sesję Claude Code, żeby kontekst nie rósł w nieskończoność. `CLAUDE.md` wczyta się sam.
2. **Prompt fazy** (wzór niżej).
3. **Przeczytaj plan** i sprawdź, czy nie rozszerza zakresu. Zatwierdź albo popraw.
4. **Lista kontrolna po fazie:**
   - [ ] `make test`, `make lint` i `make docs-check` przechodzą.
   - [ ] `make up` uruchamia aplikację, a Ty przeklikałeś nowe funkcje ręcznie.
   - [ ] Przejrzałeś diff (zwłaszcza uprawnienia, SQL widoczności, obsługa plików).
   - [ ] Dokumentacja w `docs/en/` i `docs/pl/` jest zaktualizowana, a w `changelog.md` jest wpis w `[Unreleased]`.
   - [ ] Odpowiednie punkty w `docs/*/README.md` są zaznaczone.
   - [ ] Commity są małe i opisane, zmiany wypchnięte na GitHub.
5. Dopiero potem następna faza.

**Wzór promptu dla fazy N:**

```
Przeczytaj CLAUDE.md oraz docs/en/README.md (roadmapa) i dokumenty powiązane z tą fazą.
Zrealizuj Fazę N: <nazwa fazy>.
Najpierw przedstaw plan (pliki, tabele, migracje, testy, dokumentacja do aktualizacji) i zadaj pytania, jeśli coś jest niejasne. Czekaj na moją akceptację.
Podczas pracy: małe commity (Conventional Commits), testy do każdej funkcji, aktualizacja docs/en i docs/pl oraz changelogu w tych samych commitach.
Na końcu raport: co zmieniono, jak to przetestować ręcznie, które dokumenty zaktualizowano, otwarte pytania.
```

**Dobre praktyki z Claude Code:**

- Jedna faza = jedna sesja. Nie mieszaj w jednej sesji OCR z logowaniem AD.
- Gdy Claude Code proponuje nową bibliotekę lub zmianę stacku: niech zapisze to jako ADR w `docs/*/decisions.md` albo odrzuć.
- Do testów używaj tylko plików syntetycznych. Prawdziwe dokumenty firmowe i maile nie trafiają do repozytorium ani do fixture'ów.
- Jeśli coś się rozjedzie z `CLAUDE.md`, popraw `CLAUDE.md`, nie obchodź go.
- Co jakiś czas poproś: „Zrób przegląd zgodności kodu z CLAUDE.md i dokumentacją, wypisz rozbieżności”.

## Krok 7. Przygotuj serwer (przed Fazą 6 lub pierwszym wdrożeniem)

1. Linux z Docker Engine i wtyczką Compose (według oficjalnej instrukcji Dockera dla Twojej dystrybucji).
2. Użytkownik do wdrożeń (nie root), należący do grupy `docker`.
3. Katalog: `sudo mkdir -p /srv/baza-wiedzy && sudo chown <uzytkownik>: /srv/baza-wiedzy`.
4. Firewall: otwarte 80 i 443 tylko dla sieci, z której mają korzystać użytkownicy (LAN/VPN). Port 22 tylko dla administratorów.
5. Rekord DNS dla nazwy aplikacji wskazujący na serwer.
6. Serwer musi widzieć kontroler domeny na porcie 636 i `ghcr.io` na 443 (albo użyj procedury offline z `docs/pl/deployment-operations.md`).
7. Token GitHuba do pobierania obrazów z GHCR: utwórz token z prawem odczytu pakietów (sprawdź w ustawieniach GitHuba, jakie typy tokenów GHCR aktualnie obsługuje). Przechowuj go w menedżerze haseł.

## Krok 8. Pierwsze wdrożenie

Szczegóły i polecenia są w `docs/pl/deployment-operations.md`, sekcja „Pierwsza instalacja”. W skrócie:

1. Otaguj wydanie (`git tag v0.1.0 && git push --tags`) i poczekaj, aż GitHub Actions zbuduje obrazy.
2. Skopiuj na serwer `docker-compose.yml`, `.env.example`, `scripts/` do `/srv/baza-wiedzy/`.
3. Utwórz i wypełnij `.env` (`chmod 600`).
4. `docker login ghcr.io`, potem `docker compose pull && docker compose up -d`.
5. `docker compose run --rm app baza-wiedzy migrate`.
6. `docker compose exec app baza-wiedzy create-admin <login>`.
7. Otwórz `https://<nazwa-DNS>/ready`: ma zwrócić OK.
8. Zaloguj się adminem lokalnym, ustaw mapowanie grup AD → role, przetestuj logowanie kontem z AD z każdej z trzech grup.

## Krok 9. Aktualizacje, backup, odtwarzanie

**Aktualizacja:**

1. Na komputerze deweloperskim: przenieś `[Unreleased]` w obu changelogach pod nową wersję, zrób commit, otaguj `vX.Y.Z`, wypchnij tag.
2. Poczekaj na zielone GitHub Actions.
3. Na serwerze: `cd /srv/baza-wiedzy && ./scripts/update.sh X.Y.Z`.
4. Sprawdź `/ready` i `data/update.log`.
5. Gdy coś pójdzie źle: skrypt sam wraca do poprzedniej wersji; ręcznie: `./scripts/rollback.sh <poprzednia-wersja>`. Jeśli wydanie miało migrację niezgodną wstecz, odtwórz backup sprzed aktualizacji.

**Backup:**

1. Dodaj do crona zadanie z `docs/pl/deployment-operations.md` (sekcja „Backup”).
2. Ustal, gdzie kopie lądują poza serwerem (NAS, inny serwer) i ustaw `BACKUP_REMOTE_CMD`. Do tego czasu nie masz pełnego backupu.
3. **Raz w miesiącu** zrób test odtwarzania na maszynie zapasowej. Zapisz datę i wynik.

## Krok 10. Test odbiorczy przed udostępnieniem użytkownikom

Przygotuj zestaw ok. 200 prawdziwych dokumentów (w środowisku testowym, nie w repozytorium):

- [ ] PDF z tekstem, PDF skany (różnej jakości), DOCX, DOC, TXT, EML z załącznikami, MSG.
- [ ] Wyszukaj frazy w różnych odmianach („faktura”, „fakturze”) i bez polskich znaków.
- [ ] Wyszukaj fragment, który jest tylko w załączniku maila.
- [ ] Wyszukaj fragment, który jest tylko na zeskanowanej stronie.
- [ ] Wyszukaj numer dokumentu lub nazwę serwera (kody).
- [ ] Reader nie widzi szkiców i nie znajduje ich wyszukiwarką.
- [ ] Publisher bez `documents.publish_any` nie publikuje cudzych dokumentów.
- [ ] Admin tworzy nową rolę, nadaje uprawnienia, przypisuje ją użytkownikowi.
- [ ] Wpisy w logu audytowym pojawiają się dla logowania, uploadu, publikacji, pobrania, zmian ról.
- [ ] Aktualizacja i rollback na kopii środowiska przeszły bez problemów.
- [ ] Odtworzenie z backupu na maszynie zapasowej działa.

Dopiero po tym zaproś pierwszych użytkowników (np. jeden dział), zbierz uwagi i poszerzaj grono.

---

## Otwarte pytania, na które warto odpowiedzieć w trakcie prac

1. Czy Publisher może publikować dokumenty wgrane przez inne osoby? (Domyślnie nie.)
2. Jak długo przechowywać dokumenty zarchiwizowane i wpisy audytowe (RODO)?
3. Wewnętrzne CA czy Let's Encrypt?
4. Gdzie trzymać kopie zapasowe i jak podpiąć monitoring (np. Zabbix na `/ready`)?
5. Czy „ok 200” z pierwszego pytania oznaczało 200 dokumentów czy 200 użytkowników? (Projekt zakłada 20 000 dokumentów i 100 użytkowników, więc w obu przypadkach jest zapas.)
6. Czy polityka firmy wymaga skanowania antywirusowego uploadów (ClamAV)?
