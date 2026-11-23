# Uwierzytelnianie, uprawnienia i audyt

> Dotyczy: v0.4.1 · Ostatnia aktualizacja: 2026-11-23

## Uwierzytelnianie

### Sesje

- Strona logowania: `/login`. Każda strona poza `/login`, `/health`, `/ready` i `/static/` wymaga sesji; bez niej przeglądarka jest przekierowywana na `/login?next=<strona>` (żądania HTMX dostają `401` z `HX-Redirect`). Po zalogowaniu użytkownik wraca do `next` (akceptowane są tylko ścieżki lokalne).
- Po udanym logowaniu serwer tworzy wiersz w `sessions` i ustawia ciasteczko `__Host-bw_session` z losowym tokenem (256 bitów); zapisywany jest tylko jego SHA-256. Flagi ciasteczka: `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, bez `Domain` (wymusza to prefiks `__Host-`).
- **Każde logowanie tworzy nową sesję** i usuwa sesję, którą przeglądarka miała wcześniej (jeśli była), więc identyfikator sesji podrzucony przed logowaniem nigdy nie zostanie użyty (fiksacja sesji).
- Limit bezczynności `SESSION_IDLE_MINUTES` (domyślnie 120) i bezwzględny czas życia `SESSION_ABSOLUTE_HOURS` (domyślnie 12). Wygasła sesja jest usuwana przy następnym użyciu; wygasłe wiersze są też czyszczone przy każdym logowaniu. Wylogowanie (`POST /logout`) usuwa sesję. Admini mogą unieważnić wszystkie sesje użytkownika (panel admina, strona użytkownika); `create-admin` unieważnia je przy resecie hasła.
- Dezaktywacja użytkownika kończy dostęp przy następnym żądaniu: każde żądanie sprawdza, czy użytkownik jest aktywny. **Użytkownik zablokowany w AD zachowuje otwartą już sesję do jej wygaśnięcia** (najwyżej `SESSION_ABSOLUTE_HOURS`): aplikacja pyta AD tylko przy logowaniu. Aby odciąć dostęp od razu, dezaktywuj użytkownika albo unieważnij jego sesje w panelu admina.
- **CSRF**: każde żądanie zmieniające stan (POST, PUT, PATCH, DELETE, także HTMX) musi nieść token `HMAC-SHA256(SECRET_KEY, sesja)` (przed logowaniem: związany z ciasteczkiem `__Host-bw_pre`). Formularze wysyłają go w ukrytym polu `csrf_token`; HTMX w nagłówku `X-CSRF-Token` (ustawionym raz na `<body>`). Odrzucane są też żądania z `Origin` innej witryny albo z `Sec-Fetch-Site: cross-site`. Odmowa: strona `403` w układzie aplikacji, „Żądanie odrzucone”, z odnośnikami „Wróć na stronę główną” i „Zaloguj ponownie”; nic nie zostaje zmienione. Każda odmowa trafia do logu jako `CSRF check refused a request: reason=<powód> method=<metoda> path=<ścieżka>` (nigdy wartości tokenów ani dane użytkownika), powody: `no_session` (brak ciasteczka sesji i ciasteczka przed logowaniem), `cross_site`, `missing_token`, `mismatch` (token innej sesji, na przykład strona otwarta podczas logowania), `session_mismatch` (to samo, ale żądanie nie ma już sesji). Formularz multipart (wgrywanie plików) bez nagłówka musi mieć `csrf_token` jako **pierwsze** pole: czytany jest tylko początek ciała (najwyżej 64 KB), pliki płyną dalej nietknięte; token za plikami jest odrzucany (ADR-032). Szablony: makro admina `csrf()` trzeba importować `with context` (`{% from "admin/_macros.html" import csrf with context %}`); pusty token powoduje błąd strony zamiast pola, które zawsze byłoby odrzucane, a `tests/integration/test_form_csrf.py` wysyła każdy formularz POST z tokenem, który sam wyrenderował.
- **Nieudane logowania** są liczone w Redisie w oknie `LOGIN_WINDOW_MINUTES` (15) dla trzech zakresów: nazwa użytkownika (`LOGIN_MAX_FAILURES_ACCOUNT`, 30), para nazwa + adres (`LOGIN_MAX_FAILURES_ACCOUNT_IP`, 5) i adres (`LOGIN_MAX_FAILURES_IP`, 20). Osiągnięcie progu blokuje dany zakres na `LOGIN_LOCKOUT_MINUTES` (15); blokada wygasa sama. Wysoki próg dla konta nie pozwala napastnikowi zablokować koledze konta z innego adresu. Blokady liczone są dla każdej wpisanej nazwy, istniejącej czy nie, więc „zbyt wiele prób” (HTTP 429) nie zdradza, czy konto istnieje. Wcześniejsze odblokowanie: `docker compose exec app baza-wiedzy unlock-login --user <nazwa>` i/lub `--ip <adres>` (audytowane jako `cli.unlock_login`). Gdy Redis nie działa, logowanie działa bez limitów, a błąd trafia do logu (`login limiter: Redis unavailable`).
- **Jedna odpowiedź na każdy błąd**: złe hasło, nieznany użytkownik, nieaktywne konto dają `401` i „Nieprawidłowa nazwa użytkownika lub hasło.” Dla nieznanych nazw serwer i tak liczy hash Argon2, więc czas odpowiedzi jest podobny. Dziennik audytu zapisuje wpisaną nazwę tylko wtedy, gdy konto istnieje (w przeciwnym razie `known_user: false`), bo ludzie czasem wpisują hasło w pole nazwy.
- **Adres klienta**: do aplikacji dochodzi się tylko przez Caddy. Caddy zastępuje `X-Forwarded-For` podany przez klienta; uvicorn akceptuje ten nagłówek tylko od stałego adresu proxy (`FORWARDED_ALLOW_IPS` = `PROXY_IP`, zob. [deployment-operations.md](deployment-operations.md#konfiguracja)). Adres służy do limitów i dziennika audytu. Łańcuch: Caddy bierze adres połączenia TCP, które odbiera (`remote_ip` w jego logu JSON), ignoruje `X-Forwarded-For` wysłany przez klienta (brak skonfigurowanych `trusted_proxies`) i przekazuje własny; uvicorn (`--proxy-headers`) używa go tylko dla połączeń z `PROXY_IP`. Sprawdzone 2026-11-17: logowanie z podrobionym `X-Forwarded-For: 203.0.113.99` trafia do audytu z prawdziwym adresem połączenia. Aplikacja jest dokładna tylko na tyle, na ile Caddy widzi klienta: w środowisku deweloperskim (Docker Engine w WSL2, przeglądarka w Windows) każde żądanie dociera do Caddy przez przekierowanie portów WSL i proxy użytkownika Dockera (userland proxy), więc Caddy, limity i audyt widzą bramę Dockera (`172.30.70.1`). Na serwerze z Linuksem połączenia z innych komputerów są przekierowywane przez DNAT iptables/nftables, który zachowuje adres klienta; sprawdź to po instalacji (Faza 7): `docker compose logs proxy | grep -o '"remote_ip":"[^"]*"' | sort | uniq -c` musi pokazywać adresy stacji roboczych. Jeśli pokazuje bramę, demon Dockera używa dla tych połączeń userland proxy: ustaw `"userland-proxy": false` w `/etc/docker/daemon.json` i zrestartuj Dockera. Load balancer albo inne proxy przed Caddy wymagałoby `trusted_proxies` w Caddyfile (nieskonfigurowane).
- **Nagłówki bezpieczeństwa** (ustawia je aplikacja na każdej odpowiedzi): `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin` oraz `Cache-Control: no-store` dla stron (nie dla `/static/`). HTMX działa z `allowEval: false`, `includeIndicatorStyles: false`, `selfRequestsOnly: true`. Caddy dodaje HSTS.
- `SECRET_KEY` (co najmniej 32 znaki) podpisuje tokeny CSRF; aplikacja odmawia startu przy słabej wartości. Zmiana klucza unieważnia otwarte formularze (użytkownik odświeża stronę), nie sesje.

### Active Directory (LDAP przez TLS)

Włączane przez `LDAP_ENABLED=true`. Przy `false` (domyślnie) logować mogą się tylko konta lokalne, aplikacja przy starcie zapisuje w logu `Active Directory login disabled (LDAP_ENABLED=false): local accounts only`, a nazwa z AD dostaje zwykły komunikat „Nieprawidłowa nazwa użytkownika lub hasło.”.

Przebieg logowania przez AD:
1. Użytkownik wpisuje nazwę i hasło na `/login`. Jeśli taką nazwę ma konto **lokalne**, sprawdzane jest tylko hasło lokalne (admin awaryjny działa zawsze). W przeciwnym razie:
2. Nazwa jest **normalizowana** (następny akapit). Limity nieudanych logowań używają nazwy znormalizowanej, więc wszystkie zapisy dzielą liczniki.
3. Puste hasło (puste albo z samych spacji) jest odrzucane **przed** jakimkolwiek zapytaniem LDAP: prosty bind LDAP z pustym hasłem może się udać jako bind anonimowy.
4. Aplikacja łączy się przez LDAPS (`LDAP_URI`; kilka adresów jest próbowanych po kolei, gdy któryś jest nieosiągalny), wykonuje bind kontem serwisowym tylko do odczytu i szuka `(&LDAP_USER_FILTER(LDAP_LOGIN_ATTRIBUTE=<nazwa>))` pod `LDAP_BASE_DN`; nazwa jest escapowana (`*`, `(`, `)`, `\`, NUL). Pasować musi dokładnie jeden wpis.
5. Konta wyłączone w AD (bit `ACCOUNTDISABLE` w `userAccountControl`) są odrzucane.
6. Aplikacja wykonuje bind jako użytkownik (DN + wpisane hasło). Sukces = hasło zweryfikowane; hasła z AD nigdy nie są zapisywane ani logowane.
7. Grupy są czytane wyszukiwaniem stronicowanym (`LDAP_PAGE_SIZE`): członkostwo bezpośrednie albo, przy `LDAP_NESTED_GROUPS=true`, wszystkie grupy osiągalne przez grupy zagnieżdżone (`member:1.2.840.113556.1.4.1941:`).
8. Wiersz użytkownika jest tworzony lub aktualizowany na bieżąco (just-in-time), z identyfikacją po `objectGUID` (konto AD po zmianie nazwy pozostaje tym samym użytkownikiem). Konto utworzone wcześniej przez admina (ta sama nazwa, jeszcze bez GUID) jest łączone przy pierwszym logowaniu. Konto lokalne nigdy nie zostaje przejęte: logowanie AD z nazwą równą nazwie konta lokalnego jest odrzucane i zapisywane jako błąd dla administratora.
9. Role z `source = ad` są przeliczane z `ad_group_role_map` (DN grup porównywane bez względu na wielkość liter); role z `source = manual` zostają. Użytkownik bez zmapowanej grupy i bez ręcznej roli loguje się, ale nie ma uprawnień (mówi o tym strona startowa); admin widzi go na liście użytkowników.

Każdy błąd (złe hasło, nieznana nazwa, konto wyłączone w AD, puste hasło) daje tę samą odpowiedź co błąd lokalny (`401`, ten sam komunikat) i liczy się do tych samych limitów.

**Normalizacja nazwy.** Przy `LDAP_LOGIN_ATTRIBUTE=sAMAccountName` (domyślnie): usuwany jest prefiks domeny `FIRMA\` i każdy `@sufiks`, reszta zamieniana na małe litery: `FIRMA\Jan`, `jan@firma.local` i `JAN` oznaczają `jan`, czyli nazwę użytkownika w aplikacji. Przy `userPrincipalName`: usuwany jest prefiks domeny, sama nazwa dostaje `@LDAP_UPN_SUFFIX`, wszystko małymi literami: `FIRMA\jan`, `jan` i `Jan@Firma.Local` oznaczają `jan@firma.local`. Założenie: **jedna domena AD** (potwierdzone, ADR-019), więc część domenowa `FIRMA\` nie jest z niczym porównywana, a użytkownicy innych zaufanych domen nie są obsługiwani.

**Bezpieczeństwo połączenia.** Akceptowane są tylko adresy `ldaps://`. Certyfikat serwera jest zawsze weryfikowany względem `LDAP_CA_FILE` (certyfikat CA w PEM), a nazwa hosta w `LDAP_URI` musi zgadzać się z certyfikatem; nie ma opcji wyłączenia weryfikacji. TLS 1.2 lub nowszy. Odwołania (referrals) nie są śledzone. Jawne limity czasu: `LDAP_CONNECT_TIMEOUT_SECONDS` (5) i `LDAP_RECEIVE_TIMEOUT_SECONDS` (10).

**Gdy nie da się zapytać AD** (żaden kontroler domeny nieosiągalny, problem z certyfikatem, konto serwisowe odrzucone): użytkownicy AD widzą „Logowanie kontem firmowym jest chwilowo niedostępne…” (HTTP 503), dziennik audytu dostaje `auth.ad_unavailable` z `reason` (`unreachable`, `tls`, `service_bind`, `error`) i bez innych szczegółów, log aplikacji dostaje błąd z przyczyną techniczną (przy problemie z certyfikatem: `TLS/certificate verification failed … Check LDAP_CA_FILE …`). Takie próby nie liczą się do limitów. Lokalny admin awaryjny działa dalej.

**AD a otwarte sesje.** AD jest pytane tylko przy logowaniu. Użytkownik wyłączony w AD albo usunięty z zmapowanej grupy zachowuje otwartą sesję i jej uprawnienia, dopóki sesja się nie skończy (najwyżej `SESSION_ABSOLUTE_HOURS`, domyślnie 12 h) albo admin nie dezaktywuje użytkownika lub nie unieważni jego sesji.

Ustawienia (środowisko, wszystkie w `.env.example`): `LDAP_ENABLED`, `LDAP_URI`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD` albo `LDAP_BIND_PASSWORD_FILE`, `LDAP_BASE_DN`, `LDAP_LOGIN_ATTRIBUTE`, `LDAP_UPN_SUFFIX`, `LDAP_USER_FILTER`, `LDAP_CA_FILE`, `LDAP_NESTED_GROUPS`, `LDAP_CONNECT_TIMEOUT_SECONDS`, `LDAP_RECEIVE_TIMEOUT_SECONDS`, `LDAP_PAGE_SIZE`. Przy `LDAP_ENABLED=true` aplikacja odmawia startu, gdy konfiguracja jest niepełna (na przykład `LDAP_URI 'ldap://…' must start with ldaps://`, `LDAP_CA_FILE … does not exist`).

Zalecane grupy AD (nazwy to przykłady; mapowanie edytuje się w panelu admina): `BazaWiedzy-Admins`, `BazaWiedzy-Publishers`, `BazaWiedzy-Readers`.

Akcje audytu logowań AD: `auth.login`, `auth.login_failed`, `auth.ad_unavailable`, `user.create` (pierwsze logowanie, `source: ad_jit`), `user.ad_link` (połączenie konta utworzonego wcześniej), `user.rename`, `user.roles_sync` (role dodane/usunięte przez zmiany grup).

### Test na prawdziwym Active Directory

Testy automatyczne używają katalogu-atrapy (ldap3 `MOCK_SYNC`), który nie obsługuje wyszukiwania grup zagnieżdżonych ani nie sprawdza prawdziwych certyfikatów. Przed pierwszym użyciem produkcyjnym przetestuj raz na prawdziwym AD, a potem ponownie po zmianie kontrolerów domeny, certyfikatów albo struktury grup.

**Dane potrzebne od administratora AD:**

| Element | Przykład | Gdzie trafia |
|---|---|---|
| Nazwy hostów 1–2 kontrolerów domeny z LDAPS (port 636) | `dc1.firma.local`, `dc2.firma.local` | `LDAP_URI=ldaps://dc1.firma.local:636,ldaps://dc2.firma.local:636` |
| Certyfikat CA (PEM), który podpisał certyfikaty DC (główny plus pośredni, jeśli jest, w jednym pliku) | `firma-root-ca.pem` | `./secrets/ldap/ca.pem` |
| Konto serwisowe tylko do odczytu: DN i hasło; hasło bez wygasania (albo przypomnienie) | `CN=svc-bazawiedzy,OU=Service Accounts,DC=firma,DC=local` | `LDAP_BIND_DN`, `./secrets/ldap/bind_password` |
| Base DN obejmujący wszystkich, którzy mają się logować | `DC=firma,DC=local` albo `OU=Staff,DC=firma,DC=local` | `LDAP_BASE_DN` |
| Forma loginu znana użytkownikom: `sAMAccountName` albo UPN, oraz sufiks UPN | `sAMAccountName`, `firma.local` | `LDAP_LOGIN_ATTRIBUTE`, `LDAP_UPN_SUFFIX` |
| DN trzech grup aplikacji | `CN=BazaWiedzy-Readers,OU=Groups,DC=firma,DC=local` | panel admina, mapowanie grup AD |
| Konta testowe (z hasłami, tylko na czas testu): **A** bezpośredni członek BazaWiedzy-Admins; **B** członek grupy X, która jest członkiem BazaWiedzy-Readers (zagnieżdżenie); **C** konto wyłączone; **D** konto aktywne bez grup aplikacji; **E** konto z wygasłym hasłem albo wymuszoną zmianą | `test-bw-a` … `test-bw-e` | kroki poniżej |

**Kroki** (na serwerze, w /srv/baza-wiedzy):
1. Pliki: `mkdir -p secrets/ldap && cp firma-root-ca.pem secrets/ldap/ca.pem && printf '%s' '<hasło>' > secrets/ldap/bind_password && sudo chown -R 10001:10001 secrets/ldap && chmod 400 secrets/ldap/*`. Sprawdzenie certyfikatu: `openssl s_client -connect dc1.firma.local:636 -CAfile secrets/ldap/ca.pem </dev/null | grep 'Verify return code'` → `Verify return code: 0 (ok)`.
2. `.env`: `LDAP_ENABLED=true`, wartości z tabeli, `LDAP_BIND_PASSWORD_FILE=/run/secrets/ldap/bind_password`, na razie `LDAP_NESTED_GROUPS=false`; `docker compose up -d app`. `docker compose logs app | grep 'Active Directory'` → `Active Directory login enabled (...)`.
3. Konto serwisowe: `docker compose exec app baza-wiedzy ad-check` → `Service account bind: OK`.
4. Wyszukanie użytkownika: `docker compose exec app baza-wiedzy ad-check --user test-bw-a` → DN, `disabled in AD: False`, grupy łącznie z BazaWiedzy-Admins. Powtórz z `FIRMA\test-bw-a` i `test-bw-a@firma.local`: ten sam `Normalized login`. Dodaj `--password`, żeby sprawdzić hasło (`User bind with the password: OK`).
5. Panel admina → Grupy AD: zmapuj DN trzech grup na Admin, Publisher, Reader. `docker compose exec app baza-wiedzy ad-check --user test-bw-a` wypisze teraz `Roles from the group mapping: Admin`.
6. Logowanie w przeglądarce: **A** loguje się i widzi panel admina; wyloguj. **D** loguje się i widzi „Twoje konto nie ma jeszcze żadnych uprawnień”; **C** i **E** dostają „Nieprawidłowa nazwa użytkownika lub hasło.”; złe hasło dla **A** daje ten sam komunikat.
7. Grupy zagnieżdżone: `ad-check --user test-bw-b` przy `LDAP_NESTED_GROUPS=false` wypisuje tylko grupę X (B nie ma roli); ustaw `LDAP_NESTED_GROUPS=true`, `docker compose up -d app`, `ad-check --user test-bw-b` wypisuje teraz także BazaWiedzy-Readers i `Roles from the group mapping: Reader`; B loguje się z rolą Reader. Zostaw `true`, jeśli firma używa grup zagnieżdżonych; zanotuj czas logowania (wyszukiwanie zagnieżdżone jest wolniejsze w dużych katalogach).
8. Zmiana grup: usuń **A** z BazaWiedzy-Admins w AD, poczekaj na replikację AD, zaloguj się ponownie jako A: panelu admina już nie ma; dziennik audytu pokazuje `user.roles_sync` z `removed: ["Admin"]`.
9. Przypadki awarii: odetnij ruch do DC albo wpisz złą nazwę hosta w `LDAP_URI` → logowanie A pokazuje „chwilowo niedostępne”, audyt `auth.ad_unavailable` (`unreachable`); wstaw zły CA do `ca.pem` → to samo z `reason: tls`, a w `docker compose logs app` komunikat `TLS/certificate verification failed`; w obu przypadkach admin awaryjny dalej może się zalogować. Przywróć konfigurację.
10. Kontrola audytu: `docker compose exec db psql -U postgres -d <APP_DB_NAME> -c "SELECT at, action, actor_label, details FROM audit_log ORDER BY id DESC LIMIT 20"`. Nigdzie nie może pojawić się hasło (także `docker compose logs app | grep -i '<hasło testowe>'` nie może niczego znaleźć).
11. Usuń albo wyłącz konta testowe **C**, **D**, **E** w AD i dezaktywuj je w panelu admina.

Zapisz w dzienniku eksploatacji datę, wynik każdego kroku i użyte wartości (bez haseł).

### Lokalny administrator awaryjny

- Tworzony poleceniem `docker compose exec app baza-wiedzy create-admin <username>`; polecenie dwa razy pyta o hasło (nigdy w argumentach, nigdy w logach). Oczekiwany wynik: `Created the local admin 'bw-admin'.` Do automatyzacji: `--password-stdin` czyta jedną linię ze standardowego wejścia, na przykład `docker compose exec -T app baza-wiedzy create-admin bw-admin --password-stdin < pw.txt`.
- **To samo polecenie resetuje hasło** istniejącego lokalnego admina: dodatkowo aktywuje konto, przywraca rolę Admin i unieważnia wszystkie jego sesje (`Password reset for the local admin 'bw-admin'; account active, Admin role ensured, 2 session(s) revoked.`).
- Wybierz nazwę, której nie ma w AD (na przykład `bw-admin`). Nazwy użytkowników są unikalne bez względu na wielkość liter, łącznie dla kont lokalnych i AD; polecenie odrzuca nazwę konta AD. Konto lokalne zawsze loguje się hasłem lokalnym, także gdy AD jest włączony.
- Zasady hasła: od 14 do 256 znaków (`LOCAL_PASSWORD_MIN_LENGTH`, `LOCAL_PASSWORD_MAX_LENGTH`), bez reguł złożoności, bez spacji na początku i końcu; Unicode jest normalizowany (NFKC). Przechowuj je w firmowym menedżerze haseł.
- Haszowane Argon2id (domyślne argon2-cffi: 3 iteracje, 64 MiB, 4 wątki). Jednocześnie liczy się najwyżej `ARGON2_MAX_CONCURRENT` (domyślnie 3) hashy na proces, co ogranicza pamięć zużywaną przez serię prób logowania.
- Przeznaczony na sytuacje awaryjne i pierwszą konfigurację. Akcje audytu: `cli.create_admin`, `cli.reset_password` (aktor `cli`).
- **Pozostałe konta lokalne** (odzyskiwanie dostępu, ADR-029): `docker compose exec app baza-wiedzy users list` wypisuje login, źródło (`local`/`ad`), aktywność, role i ostatnie logowanie (Europe/Warsaw) — bez hashy, e-maili i DN. `docker compose exec app baza-wiedzy users set-password <login>` ustawia nowe hasło konta **lokalnego**: pytane dwa razy (albo `--password-stdin`), nigdy jako argument, ta sama polityka co wyżej; unieważnia wszystkie sesje konta, czyści jego blokady logowania i zapisuje `user.password_reset_cli` (bez hasła i hasha). Oczekiwany wynik: `Password changed for 'jan'; 2 session(s) revoked; login locks cleared (3 key(s)).` Nie aktywuje konta ani nie zmienia ról (do tego `user-active` i panel admina; admin awaryjny nadal używa `create-admin`). Dla konta AD odmawia: `error: 'jkowalski' is an Active Directory account: its password is managed in Active Directory, not here. Nothing was changed.`
- Świadomie **nie ma resetu hasła mailem**: konta firmowe są w Active Directory (z własnymi procedurami haseł), a nieliczne konta lokalne resetuje operator na serwerze.

## Model uprawnień

Uprawnienia to stałe kody zdefiniowane w aplikacji. Role są zestawami uprawnień. Uprawnienia efektywne użytkownika to suma wszystkich jego ról. Admini mogą w interfejsie tworzyć nowe role i wybierać ich uprawnienia; nie mogą wymyślać nowych kodów uprawnień.

### Katalog uprawnień

| Kod | Pozwala na |
|---|---|
| `documents.read` | Wyszukiwanie, podgląd i pobieranie opublikowanych dokumentów |
| `documents.upload` | Wgrywanie dokumentów i nowych wersji (zaczynają jako szkice) |
| `documents.publish` | Publikowanie i wycofywanie własnych dokumentów |
| `documents.publish_any` | Publikowanie i wycofywanie dokumentów wgranych przez innych |
| `documents.read_drafts` | Widzenie i wyszukiwanie wszystkich szkiców |
| `documents.edit_any` | Edycję metadanych, archiwizację i przywracanie cudzych dokumentów |
| `documents.delete` | Trwałe usuwanie dokumentów (wymóg RODO) |
| `categories.manage` | Tworzenie, zmianę nazw i usuwanie kategorii |
| `users.manage` | Zarządzanie użytkownikami, nadawanie ról, unieważnianie sesji |
| `roles.manage` | Tworzenie i edycję ról, mapowanie grup AD na role |
| `audit.view` | Przeglądanie logu audytowego |
| `settings.manage` | Zmianę ustawień aplikacji (limity, niesekretne ustawienia AD) |
| `system.view` | Podgląd wersji, kolejki i stanu zdrowia |

Katalog jest składany z manifestów włączonych modułów: rdzeń dostarcza `users.manage`, `roles.manage`, `audit.view`, `settings.manage` i `system.view`; moduł `documents` dostarcza `documents.*` oraz `categories.manage`. Uprawnień wyłączonego modułu nie ma w katalogu i są pomijane w rolach (zapisane przypisania zostają, nie są kasowane). Kody pozostają stałe w kodzie: moduł dodaje kody w swoim manifeście, admin nigdy ich nie wymyśla.

**Uprawnienia wymagane** (ADR-025): manifest może zadeklarować, że kod wymaga w tej samej roli innego kodu. `documents.publish_any` i `documents.edit_any` wymagają `documents.read_drafts` (publikowanie czy archiwizowanie cudzych szkiców jest niemożliwe bez ich widzenia, a żaden z tych kodów sam szkiców nie pokazuje). Edytor ról pokazuje „wymaga: …” przy takim kodzie i odmawia zapisania roli bez wymaganego kodu („Uprawnienie X wymaga też Y.”). Role zapisane przed wprowadzeniem reguły nie są zmieniane automatycznie: lista ról oznacza je jako „niespójna”, a strona roli wymienia brakujące kody; dopóki nie zostaną poprawione, ich posiadacze po prostu nie widzą cudzych szkiców. Role domyślne spełniają wszystkie wymagania (test).

### Role domyślne

| Rola | Uprawnienia |
|---|---|
| Reader (w UI: Czytelnik) | `documents.read` |
| Publisher (w UI: Publikujący) | `documents.read`, `documents.upload`, `documents.publish` |
| Admin (w UI: Administrator) | wszystkie uprawnienia (niezmienna rola systemowa) |

Wszystkie trzy to role systemowe: nie można ich usunąć ani zmienić ich nazwy (UI pokazuje nazwy przetłumaczone). Uprawnienia ról Reader i Publisher admin może dostosować. Admin zawsze ma pełny katalog, także kody modułów dodanych później, i jest tylko do odczytu w macierzy uprawnień.

Zasady wymuszane na serwerze (tak samo w panelu admina i w CLI), każda z czytelnym komunikatem:
- admin nie może dezaktywować własnego konta ani odebrać mu roli Admin;
- ostatniego aktywnego użytkownika z rolą Admin (z dowolnego źródła) nie można dezaktywować ani pozbawić roli Admin;
- usunięcie roli własnej, którą mają użytkownicy, pokazuje listę dotkniętych użytkowników i wymaga jawnego potwierdzenia;
- dezaktywacja użytkownika od razu unieważnia wszystkie jego sesje; zmiany ról i uprawnień działają od następnego żądania użytkownika (uprawnienia są czytane z bazy przy każdym żądaniu).

### Panel admina

`/admin` (link „Administracja” w nagłówku, widoczny tylko dla użytkowników z co najmniej jednym uprawnieniem administracyjnym; serwer i tak sprawdza uprawnienie na każdej trasie). Nawigacja jest budowana z manifestów modułów (`MenuItem` z `section="admin"`), więc moduły mogą dodawać strony; pozycja jest widoczna tylko dla użytkowników z jej uprawnieniem.

| Strona | Uprawnienie | Co robi |
|---|---|---|
| Użytkownicy `/admin/users` | `users.manage` | Lista z wyszukiwaniem (nazwa, nazwa wyświetlana, e-mail), filtrami (źródło, aktywność) i stronami po 25. **Dodaj z wyprzedzeniem użytkownika AD**: nazwa i role; nazwa jest normalizowana jak login AD (`FIRMA\Nowak` → `nowak`) i łączona z kontem AD przy jego pierwszym logowaniu; konta lokalne tworzy wyłącznie `create-admin`. Strona użytkownika: role nadane w aplikacji (role z grup AD widoczne tylko do odczytu), aktywacja/dezaktywacja, unieważnienie wszystkich sesji. Dane z AD (nazwa, e-mail, DN) są tylko do odczytu |
| Role `/admin/roles` | `roles.manage` | Lista z liczbą użytkowników; nowa rola; edycja nazwy, opisu i macierzy uprawnień (kody pogrupowane według modułów); usunięcie z listą dotkniętych użytkowników |
| Grupy AD `/admin/ad-groups` | `roles.manage` | Mapowanie DN grupy na rolę. DN musi być poprawny (`CN=…` albo `OU=…` z częściami `DC=`). Mapowanie na Admin wymaga zaznaczenia potwierdzenia po ostrzeżeniu. Przy włączonym AD pole „Sprawdź, czy grupa istnieje w Active Directory” najpierw szuka grupy: nie znaleziono → odmowa; AD nieosiągalne → zapis bez sprawdzenia, o czym strona informuje |
| System `/admin/system` | `system.view` | Wersja, moduły, liczba użytkowników i sesji, sprawdzenia `/ready`, ustawienia AD (bez sekretów) |

Formularze to zwykłe formularze HTML wysyłane z polem CSRF, więc każda czynność działa bez JavaScriptu; HTMX tylko odświeża tabele podczas wpisywania w filtr. Wartości z AD (nazwy, DN) są zawsze escapowane w HTML. Czas jest pokazywany w strefie Europe/Warsaw.

Akcje audytu zapisywane w tej samej transakcji co zmiana: `user.create` (`source: ad_planned`), `user.activate`, `user.deactivate` (`revoked_sessions`), `user.roles_change` (nazwy ról `before`/`after`), `session.revoke`, `role.create`, `role.update` (nazwa i opis `before`/`after`), `role.permissions_change` (kody `before`, `after`, `added`, `removed`), `role.delete` (uprawnienia i `affected_users`), `ad_map.create` (DN, rola, `verification`: `found`/`not_checked`/`unavailable`), `ad_map.delete`.

Wiersz poleceń (`docker compose exec app baza-wiedzy …`):

| Polecenie | Cel |
|---|---|
| `create-admin <nazwa> [--password-stdin]` | Utworzenie lokalnego admina awaryjnego albo reset jego hasła |
| `users list` | Login, źródło, aktywność, role, ostatnie logowanie każdego konta |
| `users set-password <nazwa> [--password-stdin]` | Nowe hasło konta lokalnego; unieważnia sesje, czyści blokady logowania |
| `user-active <nazwa> on\|off` | Aktywacja albo dezaktywacja użytkownika (te same zasady co w panelu; `off` unieważnia sesje). Przykład: `User 'jan' is now inactive, 2 session(s) revoked.` |
| `unlock-login [--user <nazwa>] [--ip <adres>]` | Usunięcie blokad po nieudanych logowaniach |
| `ad-check [--user <nazwa>] [--password]` | Test konfiguracji AD tylko do odczytu |

Jeśli żadne konto admina nie nadaje się do użycia, `create-admin` przywraca dostęp (aktywuje konto lokalne i nadaje mu rolę Admin).

### Widoczność dokumentów

| Stan dokumentu | Kto go widzi |
|---|---|
| Szkic | jego właściciel, posiadacze `documents.read_drafts`, Admin |
| Opublikowany | jego właściciel, każdy z `documents.read` |
| Zarchiwizowany | jego właściciel, posiadacze `documents.edit_any`, Admin; nigdy Czytelnicy |

Właściciel widzi swoje dokumenty we wszystkich stanach. **Autorów** cudzych dokumentów (kolumna „Autor” w „Moich dokumentach”, autor w wynikach wyszukiwania, filtr autora) widzą tylko posiadacze `documents.read_drafts`, `documents.publish_any` albo `documents.edit_any` (ADR-030); dla pozostałych autor nie jest nawet odczytywany z bazy, a parametr `author` w adresie jest ignorowany, więc nikt nie może sondować kont. `documents.publish_any` i `documents.edit_any` same nie pokazują cudzych szkiców (wymagają `documents.read_drafts`, patrz wyżej). Jest to wymuszane w SQL dla list, strony dokumentu, wyszukiwania i pobierania (`documents/visibility.py`). Testy obejmują macierz rola × akcja.

„Przetwórz ponownie”: właściciel z `documents.upload` dla własnych dokumentów, posiadacze `documents.edit_any` dla każdego dokumentu, który widzą; pozostali dostają 404 (dokument niewidoczny) albo 403. Wyodrębniony tekst i nagłówki maila widzi każdy, kto widzi dokument.

### Publikowanie, archiwizacja i pobieranie

Przyciski na stronie dokumentu (zwykłe formularze z polem CSRF; działają bez JavaScriptu). Użytkownik działa tylko na dokumentach, które widzi (w przeciwnym razie 404):

| Akcja | Ze stanu → do stanu | Własny dokument | Cudzy dokument |
|---|---|---|---|
| Publikuj | szkic → opublikowany | `documents.publish` | `documents.publish_any` |
| Cofnij publikację | opublikowany → szkic | `documents.publish` | `documents.publish_any` |
| Archiwizuj | szkic lub opublikowany → zarchiwizowany | `documents.publish` | `documents.edit_any` |
| Przywróć | zarchiwizowany → szkic | `documents.publish` | `documents.edit_any` |

Dokument widoczny, ale brak uprawnienia: 403 z komunikatem. Jeśli stan zmienił się po wyświetleniu strony (ktoś inny wykonał akcję), akcja jest odrzucana kodem 409 „Stan dokumentu zmienił się w międzyczasie”. Publikacja nie zależy od przetwarzania: strona ostrzega, gdy treści nie można wyszukać albo tylko częściowo (przetwarzanie oczekuje, zakończyło się błędem albo „wymaga OCR”). Publikacja zapisuje opublikowaną wersję (bieżącą), czas i osobę; cofnięcie publikacji i przywrócenie je czyszczą; archiwizacja zachowuje opublikowaną wersję. Role domyślne: Publikujący publikuje, cofa publikację, archiwizuje i przywraca własne dokumenty; Admin wszystkie; Czytelnik żadnych.

**Pobieranie** („Pobierz oryginał”, `/documents/<id>/download`): każdy, kto widzi dokument; opublikowana wersja dokumentów opublikowanych i zarchiwizowanych, najnowsza wersja szkiców. Odpowiedź ma zawsze `application/octet-stream` z `Content-Disposition: attachment` (przeglądarka zapisuje plik i nigdy nie wyświetla go w aplikacji), `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox` i `Cache-Control: private, no-store`; bez pobierania częściowego (Range). Nazwa pliku to oczyszczona nazwa z wgrania (z rozszerzeniem wykrytego typu, jeśli było inne), wysyłana jako zamiennik ASCII (`Łódź` → `Lodz`) i jako dokładna nazwa w UTF-8. Podgląd w przeglądarce: Faza 5.

## Log audytowy

Tabela `audit_log` tylko do dopisywania (wyzwalacz odrzuca `UPDATE`, `DELETE` i `TRUNCATE`; zob. [data-model.md](data-model.md)). Każdy wpis: czas (UTC), wykonawca (`actor_user_id` i `actor_label`: nazwa użytkownika w tamtej chwili, `cli` albo `anonymous`), akcja, obiekt (`entity_type`, `entity_id`), adres IP klienta i mały obiekt `details`. Wpisy powstają w tej samej transakcji co opisywana zmiana. `details` nigdy nie zawiera haseł, tokenów, wartości CSRF ani treści dokumentów: zapis takich kluczy jest odrzucany.

Akcje zapisywane od Fazy 1:

| Obszar | Akcje |
|---|---|
| Logowanie | `auth.login`, `auth.login_failed` (`reason`: `bad_credentials`, `inactive`, `locked`, `local_name_conflict`, ...; `known_user`), `auth.lockout` (`scope`), `auth.logout`, `auth.ad_unavailable` (`reason`) |
| Użytkownicy | `user.create` (`source`: `ad_jit` albo `ad_planned`), `user.ad_link`, `user.rename`, `user.activate`, `user.deactivate` (`revoked_sessions`), `user.roles_change` (`before`/`after`), `user.roles_sync` (grupy AD: `added`/`removed`), `session.revoke` |
| Role | `role.create`, `role.update` (`before`/`after`), `role.permissions_change` (`before`, `after`, `added`, `removed`), `role.delete` (`affected_users`) |
| Mapowanie AD | `ad_map.create` (`verification`), `ad_map.delete` |
| Audyt | `audit.export` (`filters`, `rows`, `truncated`) |
| Dokumenty | `document.upload` (`version_id`, `sha256`, `size_bytes`, `kind`; bez nazwy pliku i treści), `document.reprocess` (`version_id`, `sha256`; wykonawca `cli` z wiersza poleceń), `document.ocr_request` (`version_id`, `pages`, `source`: `button`, `cli` albo `cli_override` dla `--ignore-page-limit`), `document.ocr_cancel` (`version_id`, `pages`), `document.publish`, `document.unpublish`, `document.archive`, `document.restore` (`version_id`, `from`, `to`), `document.download` (`version_id`; tylko po pozytywnym sprawdzeniu, odmowa nie jest zapisywana) |
| Wiersz poleceń | `cli.create_admin`, `cli.reset_password`, `cli.unlock_login` (wykonawca `cli`) |

Reset hasła z wiersza poleceń: `user.password_reset_cli` (`username`, `revoked_sessions`). Wyszukiwania nie są audytowane, a wyszukiwane frazy nigdy nie są zapisywane (ADR-025). Kolejne akcje dokumentów (`delete`, zmiany wersji) dojdą w Fazie 5.

**Przeglądarka** `/admin/audit` (uprawnienie `audit.view`, menu „Dziennik audytu”): od najnowszych, 50 na stronę, filtry: zakres dat (dni lokalne, Europe/Warsaw), wykonawca (zawiera), akcja (dokładna nazwa albo prefiks zakończony kropką, np. `role.`), obiekt (typ lub identyfikator, zawiera). `details` jest pokazywane jako escapowane pary klucz–wartość (klucze zagnieżdżone jako `before.name`, listy po przecinku), nigdy jako surowy JSON.

**Eksport CSV**: przycisk „Eksportuj CSV (bieżące filtry)” wysyła bieżące filtry (z CSRF). Plik `audit-RRRRMMDD-GGMM.csv` jest w UTF-8 z BOM, separator `;` (otwiera się bezpośrednio w arkuszu z polskimi ustawieniami), od najnowszych, kolumny `id;time_utc;time_local;actor;actor_user_id;action;entity_type;entity_id;ip;details`. Komórki zaczynające się od `=`, `+`, `-`, `@`, tabulatora albo powrotu karetki dostają przedrostek `'`, żeby arkusz nie wykonał ich jako formuły (CSV injection). Najwyżej `AUDIT_EXPORT_MAX_ROWS` (domyślnie 10 000) wierszy: przeglądarka ostrzega przed eksportem, gdy przefiltrowany wynik jest większy, a obcięty plik kończy się linią `TRUNCATED: only the newest N entries; narrow the filters for the rest` (nagłówek odpowiedzi `X-Export-Truncated: true`). Każdy eksport sam trafia do audytu jako `audit.export` z filtrami, liczbą wierszy i informacją o obcięciu. Wyeksportowany plik zawiera dane osobowe (następna sekcja): przechowuj go odpowiednio i usuń, gdy przestanie być potrzebny.

### RODO

Dane osobowe przetwarzane w Fazie 1 (jeszcze bez treści dokumentów):

| Gdzie | Dane | Cel | Jak długo |
|---|---|---|---|
| `users` | nazwa użytkownika, nazwa wyświetlana, e-mail, DN i objectGUID z AD, flaga aktywności, czas ostatniego logowania | identyfikacja użytkowników, uprawnienia | dopóki konto istnieje (konta są dezaktywowane, nie usuwane) |
| `sessions` | hash tokenu sesji, adres IP, user agent przeglądarki, czasy | utrzymanie zalogowania, unieważnianie sesji | do wylogowania, unieważnienia albo wygaśnięcia (najwyżej `SESSION_ABSOLUTE_HOURS`); wygasłe wiersze są usuwane |
| `audit_log` | nazwa i identyfikator wykonawcy, adres IP, akcja, obiekt, szczegóły (nazwy ról, kody uprawnień, wpisana nazwa użytkownika tylko dla istniejących kont) | bezpieczeństwo, rozliczalność (kto zmienił uprawnienia, kto się logował), dowody na potrzeby RODO | **retencja jeszcze nieustalona** (otwarte pytanie 2 w [decisions.md](decisions.md)); dziś nic nie jest usuwane |
| Redis | zahashowana nazwa użytkownika i adres IP w licznikach nieudanych logowań | ochrona przed zgadywaniem haseł | najwyżej `LOGIN_WINDOW_MINUTES` + `LOGIN_LOCKOUT_MINUTES` |
| Logi kontenerów | `app`: adres IP i żądana ścieżka (log dostępu). `proxy` (Caddy, log dostępu w JSON): adres IP, ścieżka i nagłówki żądania, w tym user agent; Caddy maskuje ciasteczka i `Authorization` | rozwiązywanie problemów | rotacja logów Dockera (5 × 10 MB na kontener) |
| Eksporty CSV | jak `audit_log` | wyjaśnianie incydentów | poza aplikacją: odpowiada osoba, która wyeksportowała |

Kto ma dostęp: posiadacze `audit.view` (przeglądarka i eksport, oba audytowane) i `users.manage` (lista użytkowników); osoby z dostępem do powłoki serwera (baza, logi, backupy). Hasła użytkowników AD nigdy nie są zapisywane; hasło konta awaryjnego jest zapisane tylko jako hash Argon2id.

Propozycja retencji do potwierdzenia z działem prawnym / Inspektorem Ochrony Danych (IOD): **dziennik audytu 24 miesiące**, potem usuwany poleceniem serwisowym (Faza 6, razem z backupami; polecenie wyłączy wyzwalacz tylko na czas tego usunięcia i zapisze to w audycie). Sesje i liczniki w Redisie już wygasają, jak opisano. Podstawę prawną i klauzulę informacyjną dla pracowników ustala IOD.

## Lista kontrolna bezpieczeństwa dla nowych funkcji

- Jakiego uprawnienia wymaga? Czy jest w katalogu i w testach? Każda trasa ma `require_user` albo `require_permission` (także fragmenty HTMX); `tests/unit/test_route_inventory.py` nie przejdzie dla trasy spoza jawnej listy publicznych (`/health`, `/ready`, `/login`, `/static` oraz `GET /logout`, który tylko przekierowuje 303 na `/login`: wylogowanie pozostaje POST-em z tokenem CSRF, więc odnośnik ani wpisany adres nikogo nie wylogują).
- Czy reguła widoczności jest w SQL?
- Czy zapisuje wpis audytowy?
- Czy każde żądanie zmieniające stan jest chronione CSRF?
- Czy wartości od użytkownika są escapowane (HTML, filtry LDAP, ścieżki plików)?
