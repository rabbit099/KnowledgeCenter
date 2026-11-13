# Uwierzytelnianie, uprawnienia i audyt

> Dotyczy: v0.1.0 · Ostatnia aktualizacja: 2026-11-13

## Uwierzytelnianie

### Sesje

- Strona logowania: `/login`. Każda strona poza `/login`, `/health`, `/ready` i `/static/` wymaga sesji; bez niej przeglądarka jest przekierowywana na `/login?next=<strona>` (żądania HTMX dostają `401` z `HX-Redirect`). Po zalogowaniu użytkownik wraca do `next` (akceptowane są tylko ścieżki lokalne).
- Po udanym logowaniu serwer tworzy wiersz w `sessions` i ustawia ciasteczko `__Host-bw_session` z losowym tokenem (256 bitów); zapisywany jest tylko jego SHA-256. Flagi ciasteczka: `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, bez `Domain` (wymusza to prefiks `__Host-`).
- **Każde logowanie tworzy nową sesję** i usuwa sesję, którą przeglądarka miała wcześniej (jeśli była), więc identyfikator sesji podrzucony przed logowaniem nigdy nie zostanie użyty (fiksacja sesji).
- Limit bezczynności `SESSION_IDLE_MINUTES` (domyślnie 120) i bezwzględny czas życia `SESSION_ABSOLUTE_HOURS` (domyślnie 12). Wygasła sesja jest usuwana przy następnym użyciu; wygasłe wiersze są też czyszczone przy każdym logowaniu. Wylogowanie (`POST /logout`) usuwa sesję. Admini mogą unieważnić wszystkie sesje użytkownika (panel admina, etap B5); `create-admin` unieważnia je przy resecie hasła.
- Dezaktywacja użytkownika kończy dostęp przy następnym żądaniu: każde żądanie sprawdza, czy użytkownik jest aktywny. **Użytkownik zablokowany w AD zachowuje otwartą już sesję do jej wygaśnięcia** (najwyżej `SESSION_ABSOLUTE_HOURS`): aplikacja pyta AD tylko przy logowaniu. Aby odciąć dostęp od razu, dezaktywuj użytkownika albo unieważnij jego sesje w panelu admina.
- **CSRF**: każde żądanie zmieniające stan (POST, PUT, PATCH, DELETE, także HTMX) musi nieść token `HMAC-SHA256(SECRET_KEY, sesja)` (przed logowaniem: związany z ciasteczkiem `__Host-bw_pre`). Formularze wysyłają go w ukrytym polu `csrf_token`; HTMX w nagłówku `X-CSRF-Token` (ustawionym raz na `<body>`). Odrzucane są też żądania z `Origin` innej witryny albo z `Sec-Fetch-Site: cross-site`. Odmowa: `403` z krótkim komunikatem („Odśwież stronę”). Upload plików (Faza 2) musi używać nagłówka.
- **Nieudane logowania** są liczone w Redisie w oknie `LOGIN_WINDOW_MINUTES` (15) dla trzech zakresów: nazwa użytkownika (`LOGIN_MAX_FAILURES_ACCOUNT`, 30), para nazwa + adres (`LOGIN_MAX_FAILURES_ACCOUNT_IP`, 5) i adres (`LOGIN_MAX_FAILURES_IP`, 20). Osiągnięcie progu blokuje dany zakres na `LOGIN_LOCKOUT_MINUTES` (15); blokada wygasa sama. Wysoki próg dla konta nie pozwala napastnikowi zablokować koledze konta z innego adresu. Blokady liczone są dla każdej wpisanej nazwy, istniejącej czy nie, więc „zbyt wiele prób” (HTTP 429) nie zdradza, czy konto istnieje. Wcześniejsze odblokowanie: `docker compose exec app baza-wiedzy unlock-login --user <nazwa>` i/lub `--ip <adres>` (audytowane jako `cli.unlock_login`). Gdy Redis nie działa, logowanie działa bez limitów, a błąd trafia do logu (`login limiter: Redis unavailable`).
- **Jedna odpowiedź na każdy błąd**: złe hasło, nieznany użytkownik, nieaktywne konto dają `401` i „Nieprawidłowa nazwa użytkownika lub hasło.” Dla nieznanych nazw serwer i tak liczy hash Argon2, więc czas odpowiedzi jest podobny. Dziennik audytu zapisuje wpisaną nazwę tylko wtedy, gdy konto istnieje (w przeciwnym razie `known_user: false`), bo ludzie czasem wpisują hasło w pole nazwy.
- **Adres klienta**: do aplikacji dochodzi się tylko przez Caddy. Caddy zastępuje `X-Forwarded-For` podany przez klienta; uvicorn akceptuje ten nagłówek tylko od stałego adresu proxy (`FORWARDED_ALLOW_IPS` = `PROXY_IP`, zob. [deployment-operations.md](deployment-operations.md#konfiguracja)). Adres służy do limitów i dziennika audytu.
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
5. Panel admina (etap B5): zmapuj DN trzech grup na Admin, Publisher, Reader. `docker compose exec app baza-wiedzy ad-check --user test-bw-a` wypisze teraz `Roles from the group mapping: Admin`.
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

### Role domyślne

| Rola | Uprawnienia |
|---|---|
| Reader | `documents.read` |
| Publisher | `documents.read`, `documents.upload`, `documents.publish` |
| Admin | wszystkie uprawnienia (niezmienna rola systemowa) |

Reader i Publisher to role systemowe: nie można ich usunąć, a ich uprawnienia admin może dostosować. Roli Admin nie można pomniejszyć, a ostatniego aktywnego Admina nie można dezaktywować ani usunąć.

### Widoczność dokumentów

| Stan dokumentu | Kto go widzi |
|---|---|
| Szkic | jego właściciel, posiadacze `documents.read_drafts`, Admin |
| Opublikowany | każdy z `documents.read` |
| Zarchiwizowany | posiadacze `documents.edit_any`, Admin |

Jest to wymuszane w SQL dla list, wyszukiwania i pobierania. Testy obejmują macierz rola × akcja.

## Log audytowy

Tabela `audit_log` tylko do dopisywania. Zapisywane akcje: `login`, `login_failed`, `logout`, `upload`, `publish`, `unpublish`, `download`, `archive`, `restore`, `delete`, `user_create`, `role_change`, `permission_change`, `setting_change`, `reprocess`.

Każdy wpis zawiera czas, użytkownika, akcję, obiekt, IP i mały obiekt `details`. Nigdy nie zawiera treści dokumentów ani haseł. Admini z `audit.view` mogą filtrować i eksportować log (CSV). Retencja to otwarte pytanie, zobacz [decisions.md](decisions.md).

## Lista kontrolna bezpieczeństwa dla nowych funkcji

- Jakiego uprawnienia wymaga? Czy jest w katalogu i w testach?
- Czy reguła widoczności jest w SQL?
- Czy zapisuje wpis audytowy?
- Czy każde żądanie zmieniające stan jest chronione CSRF?
- Czy wartości od użytkownika są escapowane (HTML, filtry LDAP, ścieżki plików)?
