# Uwierzytelnianie, uprawnienia i audyt

> Dotyczy: v0.0.0 (przed pierwszym wydaniem) · Ostatnia aktualizacja: 2026-11-12

## Uwierzytelnianie

### Sesje

- Po udanym logowaniu serwer tworzy wiersz w `sessions` i ustawia ciasteczko z losowym tokenem (zapisywany jest tylko jego skrót).
- Ciasteczko: `HttpOnly`, `Secure`, `SameSite=Lax`.
- Limit bezczynności i bezwzględny czas życia są konfigurowalne (domyślnie 2 h bezczynności, 8 h łącznie). Admini mogą unieważniać sesje.
- Każde żądanie zmieniające stan (także HTMX) niesie token CSRF.
- Próby logowania są limitowane na konto i na adres IP; niepowodzenia trafiają do logu audytowego.

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

- Tworzony poleceniem `docker compose exec app baza-wiedzy create-admin <username>`.
- Hasło haszowane argon2id.
- Przeznaczony na sytuacje awaryjne i pierwszą konfigurację; jego użycie jest audytowane. Zalecane silne hasło w firmowym menedżerze haseł.

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
