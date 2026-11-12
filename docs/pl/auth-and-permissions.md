# Uwierzytelnianie, uprawnienia i audyt

> Dotyczy: v0.1.0 · Ostatnia aktualizacja: 2026-11-12

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

Przebieg:
1. Użytkownik wpisuje nazwę z AD (sAMAccountName lub UPN) i hasło.
2. Aplikacja łączy się przez LDAPS kontem serwisowym tylko do odczytu i znajduje wpis użytkownika (nazwa jest escapowana w filtrze).
3. Aplikacja wykonuje bind z DN użytkownika i wpisanym hasłem. Sukces = hasło zweryfikowane; aplikacja nigdy nie przechowuje haseł z AD.
4. Aplikacja czyta grupy użytkownika (opcjonalnie z grupami zagnieżdżonymi) i mapuje je na role przez `ad_group_role_map`.
5. Wiersz użytkownika jest tworzony lub aktualizowany (just-in-time). Role z `source = ad` są przeliczane przy każdym logowaniu; role z `source = manual` zostają.
6. Użytkownicy wyłączeni w AD nie mogą się zalogować.

Ustawienia (środowisko): `LDAP_URI` (`ldaps://…`), `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD` (zmienna lub sekret Dockera), `LDAP_BASE_DN`, `LDAP_USER_FILTER`, `LDAP_CA_FILE` (certyfikat firmowego CA), `LDAP_NESTED_GROUPS` (true/false), `LDAP_TIMEOUT_SECONDS`.

Zalecane grupy AD (nazwy to przykłady, ustawiane w panelu admina): `BazaWiedzy-Admins`, `BazaWiedzy-Publishers`, `BazaWiedzy-Readers`. Użytkownik bez żadnej zmapowanej grupy i bez ręcznie nadanej roli nie ma dostępu.

Gdy AD jest nieosiągalne, użytkownicy AD nie mogą się zalogować; lokalny administrator awaryjny nadal może.

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
