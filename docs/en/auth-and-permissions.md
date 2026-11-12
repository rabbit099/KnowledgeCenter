# Authentication, permissions and audit

> Applies to: v0.1.0 · Last reviewed: 2026-11-12

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

Flow:
1. The user enters the AD user name (sAMAccountName or UPN) and password.
2. The app connects over LDAPS with a read-only service account and finds the user entry (the user name is escaped in the filter).
3. The app binds with the user's DN and the entered password. Success = password verified; the app never stores AD passwords.
4. The app reads the user's groups (optionally including nested groups) and maps them to roles through `ad_group_role_map`.
5. The user row is created or updated (just-in-time). Roles with `source = ad` are recalculated on every login; roles with `source = manual` are kept.
6. Users disabled in AD cannot log in.

Settings (environment): `LDAP_URI` (`ldaps://…`), `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD` (env or Docker secret), `LDAP_BASE_DN`, `LDAP_USER_FILTER`, `LDAP_CA_FILE` (company CA certificate), `LDAP_NESTED_GROUPS` (true/false), `LDAP_TIMEOUT_SECONDS`.

Recommended AD groups (names are examples, set in the admin UI): `BazaWiedzy-Admins`, `BazaWiedzy-Publishers`, `BazaWiedzy-Readers`. A user without any mapped group and without a manual role gets no access.

If AD is unreachable, AD users cannot log in; the local break-glass admin still can.

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
