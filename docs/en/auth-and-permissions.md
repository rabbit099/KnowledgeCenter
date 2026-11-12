# Authentication, permissions and audit

> Applies to: v0.0.0 (pre-release) · Last reviewed: 2026-11-12

## Authentication

### Sessions

- After a successful login the server creates a row in `sessions` and sets a cookie containing a random token (only its hash is stored).
- Cookie: `HttpOnly`, `Secure`, `SameSite=Lax`.
- Idle timeout and absolute lifetime are configurable (defaults: 2 h idle, 8 h absolute). Admins can revoke sessions.
- Every state-changing request (including HTMX requests) carries a CSRF token.
- Login attempts are rate-limited per account and per IP; failures are written to the audit log.

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

- Created with `docker compose exec app baza-wiedzy create-admin <username>`.
- Password hashed with argon2id.
- Intended for emergencies and first setup; its use is audited. A strong password stored in the company password manager is recommended.

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
