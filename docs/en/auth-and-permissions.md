# Authentication, permissions and audit

> Applies to: v0.1.0 · Last reviewed: 2026-11-13

## Authentication

### Sessions

- Login page: `/login`. Every page except `/login`, `/health`, `/ready` and `/static/` needs a session; without one the browser is redirected to `/login?next=<page>` (HTMX requests get `401` with `HX-Redirect`). After login the user returns to `next` (only local paths are accepted).
- After a successful login the server creates a row in `sessions` and sets the cookie `__Host-bw_session` with a random token (256 bits); only its SHA-256 is stored. Cookie flags: `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, no `Domain` (the `__Host-` prefix enforces this).
- **Every login creates a new session** and deletes the session the browser had before (if any), so a session id planted before login is never used (session fixation).
- Idle limit `SESSION_IDLE_MINUTES` (default 120) and absolute lifetime `SESSION_ABSOLUTE_HOURS` (default 12). An expired session is deleted at its next use; expired rows are also purged at every login. Logout (`POST /logout`) deletes the session. Admins can revoke all sessions of a user (admin panel, stage B5); `create-admin` revokes them on a password reset.
- Deactivating a user (or a failed AD check at the next login) ends access at the next request: every request checks that the user is active. **A user disabled in AD keeps an already open session until it expires** (at most `SESSION_ABSOLUTE_HOURS`): the application asks AD only at login. To cut access at once, deactivate the user or revoke their sessions in the admin panel.
- **CSRF**: every state-changing request (POST, PUT, PATCH, DELETE, including HTMX) must carry a token: `HMAC-SHA256(SECRET_KEY, session)` (before login: bound to the cookie `__Host-bw_pre`). Forms send it in the hidden field `csrf_token`; HTMX sends the header `X-CSRF-Token` (set once on `<body>`). Requests with another site's `Origin` or `Sec-Fetch-Site: cross-site` are refused as well. Refusal: `403` with a short message ("Odśwież stronę"). File uploads (Phase 2) must use the header.
- **Failed logins** are counted in Redis in a window of `LOGIN_WINDOW_MINUTES` (15) for three scopes: the user name (`LOGIN_MAX_FAILURES_ACCOUNT`, 30), the pair user name + address (`LOGIN_MAX_FAILURES_ACCOUNT_IP`, 5) and the address (`LOGIN_MAX_FAILURES_IP`, 20). Reaching a threshold locks that scope for `LOGIN_LOCKOUT_MINUTES` (15); the lock expires by itself. The high account threshold keeps an attacker from locking a colleague out from another address. Locks are counted for every typed name, existing or not, so "too many attempts" (HTTP 429) says nothing about whether an account exists. Unlock earlier: `docker compose exec app baza-wiedzy unlock-login --user <name>` and/or `--ip <address>` (audited as `cli.unlock_login`). If Redis is down, logins still work without limits and the error is logged (`login limiter: Redis unavailable`).
- **One answer for every failure**: wrong password, unknown user, inactive account all give `401` and "Nieprawidłowa nazwa użytkownika lub hasło." For unknown names the server still computes an Argon2 hash, so the response time is similar. The audit log stores the typed name only if the account exists (`known_user: false` otherwise), because people sometimes type a password into the name field.
- **Client address**: the app is reached only through Caddy. Caddy replaces any client-supplied `X-Forwarded-For`; uvicorn accepts that header only from the proxy's fixed address (`FORWARDED_ALLOW_IPS` = `PROXY_IP`, see [deployment-operations.md](deployment-operations.md#configuration)). The address is used for limits and the audit log.
- **Security headers** (set by the application on every response): `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin` and `Cache-Control: no-store` for pages (not for `/static/`). HTMX runs with `allowEval: false`, `includeIndicatorStyles: false`, `selfRequestsOnly: true`. Caddy adds HSTS.
- `SECRET_KEY` (at least 32 characters) signs the CSRF tokens; the application refuses to start with a weak value. Changing it invalidates open forms (users reload the page), not sessions.

### Active Directory (LDAP over TLS)

Enabled with `LDAP_ENABLED=true`. When it is `false` (default), only local accounts can log in, the application logs `Active Directory login disabled (LDAP_ENABLED=false): local accounts only` at start, and an AD name gets the usual "Nieprawidłowa nazwa użytkownika lub hasło.".

Flow of an AD login:
1. The user types a name and password on `/login`. If a **local** account has that name, only the local password is checked (the break-glass admin always works). Otherwise:
2. The name is **normalized** (next paragraph). The failed-login limits use the normalized name, so all spellings share the counters.
3. A blank password (empty or only spaces) is refused **before** any LDAP request: an LDAP simple bind with an empty password can succeed as an anonymous bind.
4. The application connects over LDAPS (`LDAP_URI`; several URIs are tried in order when one is unreachable), binds with the read-only service account and searches `(&LDAP_USER_FILTER(LDAP_LOGIN_ATTRIBUTE=<name>))` under `LDAP_BASE_DN`; the name is escaped (`*`, `(`, `)`, `\`, NUL). Exactly one entry must match.
5. Accounts disabled in AD (`userAccountControl` bit `ACCOUNTDISABLE`) are refused.
6. The application binds as the user (DN + typed password). Success = password verified; AD passwords are never stored or logged.
7. Groups are read with a paged search (`LDAP_PAGE_SIZE`): direct membership, or with `LDAP_NESTED_GROUPS=true` all groups reached through nested groups (`member:1.2.840.113556.1.4.1941:`).
8. The user row is created or updated just in time, identified by `objectGUID` (a renamed AD account stays the same user). An account created in advance by an admin (same name, no GUID yet) is linked at the first login. A local account is never taken over: an AD login whose name equals a local account's name is refused and logged as an error for the administrator.
9. Roles with `source = ad` are recalculated from `ad_group_role_map` (group DNs compared ignoring case); roles with `source = manual` are kept. A user without any mapped group and without a manual role logs in but has no permissions (the start page says so); an admin sees them in the user list.

Every failure (wrong password, unknown name, disabled in AD, blank password) gives the same answer as a local failure (`401`, same message) and counts towards the same limits.

**Name normalization.** With `LDAP_LOGIN_ATTRIBUTE=sAMAccountName` (default): the domain prefix `FIRMA\` and any `@suffix` are removed and the rest is lower-cased: `FIRMA\Jan`, `jan@firma.local` and `JAN` all mean `jan`, the application user name. With `userPrincipalName`: the domain prefix is removed, a bare name gets `@LDAP_UPN_SUFFIX`, everything is lower-cased: `FIRMA\jan`, `jan` and `Jan@Firma.Local` all mean `jan@firma.local`. Assumption: **one AD domain** (confirmed, ADR-019), so the domain part of `FIRMA\` is not checked against anything and users of other trusted domains are not supported.

**Security of the connection.** Only `ldaps://` URIs are accepted. The server certificate is always verified against `LDAP_CA_FILE` (CA certificate in PEM) and the host name in `LDAP_URI` must match the certificate; there is no option to turn verification off. TLS 1.2 or newer. Referrals are not followed. Explicit timeouts: `LDAP_CONNECT_TIMEOUT_SECONDS` (5) and `LDAP_RECEIVE_TIMEOUT_SECONDS` (10).

**When AD cannot be asked** (no domain controller reachable, certificate problem, service account refused): AD users see "Logowanie kontem firmowym jest chwilowo niedostępne…" (HTTP 503), the audit log gets `auth.ad_unavailable` with `reason` (`unreachable`, `tls`, `service_bind`, `error`) and no other details, the application log gets an error with the technical cause (for a certificate problem: `TLS/certificate verification failed … Check LDAP_CA_FILE …`). These attempts do not count towards the limits. The local break-glass admin keeps working.

**AD and open sessions.** AD is asked only at login. A user disabled in AD, or removed from a mapped group, keeps an open session and its permissions until the session ends (at most `SESSION_ABSOLUTE_HOURS`, default 12 h) or an admin deactivates the user or revokes their sessions.

Settings (environment, all listed in `.env.example`): `LDAP_ENABLED`, `LDAP_URI`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD` or `LDAP_BIND_PASSWORD_FILE`, `LDAP_BASE_DN`, `LDAP_LOGIN_ATTRIBUTE`, `LDAP_UPN_SUFFIX`, `LDAP_USER_FILTER`, `LDAP_CA_FILE`, `LDAP_NESTED_GROUPS`, `LDAP_CONNECT_TIMEOUT_SECONDS`, `LDAP_RECEIVE_TIMEOUT_SECONDS`, `LDAP_PAGE_SIZE`. With `LDAP_ENABLED=true` the application refuses to start when the configuration is incomplete (for example `LDAP_URI 'ldap://…' must start with ldaps://`, `LDAP_CA_FILE … does not exist`).

Recommended AD groups (names are examples; the mapping is edited in the admin panel): `BazaWiedzy-Admins`, `BazaWiedzy-Publishers`, `BazaWiedzy-Readers`.

Audit actions of AD logins: `auth.login`, `auth.login_failed`, `auth.ad_unavailable`, `user.create` (first login, `source: ad_jit`), `user.ad_link` (account created in advance linked), `user.rename`, `user.roles_sync` (roles added/removed by group changes).

### Testing with a real Active Directory

Automated tests use a mock directory (ldap3 `MOCK_SYNC`), which cannot evaluate nested-group searches or check real certificates. Before the first production use, test against the real AD once, and again after changes of domain controllers, certificates or the group structure.

**Data needed from the AD administrator:**

| Item | Example | Goes to |
|---|---|---|
| Host names of 1-2 domain controllers with LDAPS (port 636) | `dc1.firma.local`, `dc2.firma.local` | `LDAP_URI=ldaps://dc1.firma.local:636,ldaps://dc2.firma.local:636` |
| CA certificate (PEM) that signed the DC certificates (root, plus intermediate if any, in one file) | `firma-root-ca.pem` | `./secrets/ldap/ca.pem` |
| Read-only service account: DN and password; password that does not expire (or a reminder) | `CN=svc-bazawiedzy,OU=Service Accounts,DC=firma,DC=local` | `LDAP_BIND_DN`, `./secrets/ldap/bind_password` |
| Base DN covering all users who should log in | `DC=firma,DC=local` or `OU=Staff,DC=firma,DC=local` | `LDAP_BASE_DN` |
| Login form users know: `sAMAccountName` or UPN, and the UPN suffix | `sAMAccountName`, `firma.local` | `LDAP_LOGIN_ATTRIBUTE`, `LDAP_UPN_SUFFIX` |
| DNs of the three application groups | `CN=BazaWiedzy-Readers,OU=Groups,DC=firma,DC=local` | admin panel, AD group mapping |
| Test accounts (with passwords, for the test only): **A** direct member of BazaWiedzy-Admins; **B** member of group X, where X is a member of BazaWiedzy-Readers (nested); **C** disabled account; **D** enabled account in no application group; **E** account with an expired or must-change password | `test-bw-a` … `test-bw-e` | the steps below |

**Steps** (on the server, in /srv/baza-wiedzy):
1. Files: `mkdir -p secrets/ldap && cp firma-root-ca.pem secrets/ldap/ca.pem && printf '%s' '<password>' > secrets/ldap/bind_password && sudo chown -R 10001:10001 secrets/ldap && chmod 400 secrets/ldap/*`. Check the certificate: `openssl s_client -connect dc1.firma.local:636 -CAfile secrets/ldap/ca.pem </dev/null | grep 'Verify return code'` → `Verify return code: 0 (ok)`.
2. `.env`: `LDAP_ENABLED=true`, the values from the table, `LDAP_BIND_PASSWORD_FILE=/run/secrets/ldap/bind_password`, `LDAP_NESTED_GROUPS=false` for now; `docker compose up -d app`. `docker compose logs app | grep 'Active Directory'` → `Active Directory login enabled (...)`.
3. Service account: `docker compose exec app baza-wiedzy ad-check` → `Service account bind: OK`.
4. User lookup: `docker compose exec app baza-wiedzy ad-check --user test-bw-a` → DN, `disabled in AD: False`, the groups including BazaWiedzy-Admins. Repeat with `FIRMA\test-bw-a` and `test-bw-a@firma.local`: the same `Normalized login`. Add `--password` to test the password (`User bind with the password: OK`).
5. Admin panel (stage B5): map the three group DNs to Admin, Publisher, Reader. `docker compose exec app baza-wiedzy ad-check --user test-bw-a` now prints `Roles from the group mapping: Admin`.
6. Browser logins: **A** logs in and sees the admin panel; log out. **D** logs in and sees "Twoje konto nie ma jeszcze żadnych uprawnień"; **C** and **E** get "Nieprawidłowa nazwa użytkownika lub hasło."; a wrong password for **A** gives the same message.
7. Nested groups: `ad-check --user test-bw-b` with `LDAP_NESTED_GROUPS=false` lists only group X (B has no role); set `LDAP_NESTED_GROUPS=true`, `docker compose up -d app`, `ad-check --user test-bw-b` now also lists BazaWiedzy-Readers and `Roles from the group mapping: Reader`; B logs in with the Reader role. Keep `true` if the company uses nested groups; note the login time (nested searches are slower on large directories).
8. Group change: remove **A** from BazaWiedzy-Admins in AD, wait for AD replication, log in again as A: the admin panel is gone; the audit log shows `user.roles_sync` with `removed: ["Admin"]`.
9. Failure cases: stop routing to the DCs or set a wrong host name in `LDAP_URI` → login of A shows "chwilowo niedostępne", audit `auth.ad_unavailable` (`unreachable`); put a wrong CA in `ca.pem` → the same with `reason: tls` and in `docker compose logs app` the message `TLS/certificate verification failed`; in both cases the break-glass admin can still log in. Restore the configuration.
10. Audit check: `docker compose exec db psql -U postgres -d <APP_DB_NAME> -c "SELECT at, action, actor_label, details FROM audit_log ORDER BY id DESC LIMIT 20"`. No passwords may appear anywhere (also `docker compose logs app | grep -i '<test password>'` must find nothing).
11. Delete or disable the test accounts **C**, **D**, **E** in AD and deactivate them in the admin panel.

Record the date, the result of each step and the values used (without passwords) in the operations log.

### Local break-glass admin

- Created with `docker compose exec app baza-wiedzy create-admin <username>`; the command asks for the password twice (never in arguments, never logged). Expected output: `Created the local admin 'bw-admin'.` For automation: `--password-stdin` reads one line from standard input, for example `docker compose exec -T app baza-wiedzy create-admin bw-admin --password-stdin < pw.txt`.
- The **same command resets the password** of an existing local admin: it also reactivates the account, restores the Admin role and revokes all its sessions (`Password reset for the local admin 'bw-admin'; account active, Admin role ensured, 2 session(s) revoked.`).
- Choose a name that does not exist in AD (for example `bw-admin`). User names are unique ignoring case across local and AD accounts; the command refuses the name of an AD account. A local account always logs in with its local password, also when AD is enabled.
- Password policy: 14 to 256 characters (`LOCAL_PASSWORD_MIN_LENGTH`, `LOCAL_PASSWORD_MAX_LENGTH`), no composition rules, no leading or trailing spaces; Unicode is normalized (NFKC). Store it in the company password manager.
- Hashed with Argon2id (argon2-cffi defaults: 3 iterations, 64 MiB, 4 lanes). At most `ARGON2_MAX_CONCURRENT` (default 3) hashes run at the same time per process, which caps the memory used by bursts of login attempts.
- Intended for emergencies and first setup. Audit actions: `cli.create_admin`, `cli.reset_password` (actor `cli`).

## Permission model

Permissions are fixed codes defined in the application. Roles are sets of permissions. A user's effective permissions are the union of all their roles. Admins can create new roles and choose their permissions in the UI; they cannot invent new permission codes.

### Permission catalog

| Code | Allows |
|---|---|
| `documents.read` | Search, view and download published documents |
| `documents.upload` | Upload documents and new versions (they start as drafts) |
| `documents.publish` | Publish and unpublish own documents |
| `documents.publish_any` | Publish and unpublish documents uploaded by others |
| `documents.read_drafts` | See and find all drafts |
| `documents.edit_any` | Edit metadata, archive and restore documents of others |
| `documents.delete` | Hard-delete documents (GDPR/RODO erasure) |
| `categories.manage` | Create, rename, delete categories |
| `users.manage` | Manage users, assign roles, revoke sessions |
| `roles.manage` | Create and edit roles, map AD groups to roles |
| `audit.view` | View the audit log |
| `settings.manage` | Change application settings (limits, AD settings that are not secret) |
| `system.view` | See version, queue and health status |

The catalog is assembled from the manifests of the enabled modules: the core provides `users.manage`, `roles.manage`, `audit.view`, `settings.manage` and `system.view`; the `documents` module provides `documents.*` and `categories.manage`. Permissions of a disabled module are absent from the catalog and ignored in roles (stored role assignments are kept, not deleted). Codes stay fixed in code: a module adds codes in its manifest, admins never invent them.

### Default roles

| Role | Permissions |
|---|---|
| Reader | `documents.read` |
| Publisher | `documents.read`, `documents.upload`, `documents.publish` |
| Admin | all permissions (immutable system role) |

Reader and Publisher are system roles: they cannot be deleted, and their permissions can be adjusted by an admin. Admin cannot be reduced and the last active Admin cannot be deactivated or removed.

### Document visibility

| Document state | Who sees it |
|---|---|
| Draft | its owner, holders of `documents.read_drafts`, Admin |
| Published | everyone with `documents.read` |
| Archived | holders of `documents.edit_any`, Admin |

This is enforced in SQL for lists, search and downloads. Tests cover the role × action matrix.

## Audit log

Append-only table `audit_log`. Recorded actions: `login`, `login_failed`, `logout`, `upload`, `publish`, `unpublish`, `download`, `archive`, `restore`, `delete`, `user_create`, `role_change`, `permission_change`, `setting_change`, `reprocess`.

Each entry stores time, user, action, entity, IP and a small `details` object. It never stores document text or passwords. Admins with `audit.view` can filter and export it (CSV). Retention is an open question, see [decisions.md](decisions.md).

## Security checklist for new features

- Which permission does it need? Is it in the catalog and in the tests?
- Is the visibility rule in the SQL?
- Does it write an audit entry?
- Is every state-changing request CSRF-protected?
- Are user-supplied values escaped (HTML, LDAP filters, file paths)?
