"""Manifest of the core (always enabled)."""

from baza_wiedzy.core.registry import MenuItem, ModuleManifest, Permission
from baza_wiedzy.core.web.i18n import N_

ADMIN = "admin"

manifest = ModuleManifest(
    name="core",
    version="0.0.0",
    migrations_branch="core",
    migrations_path="baza_wiedzy.core.db.migrations",
    tasks=("baza_wiedzy.core.tasks:ping",),
    permissions=(
        Permission("users.manage", N_("Manage users, assign roles, revoke sessions")),
        Permission("roles.manage", N_("Create and edit roles, map AD groups to roles")),
        Permission("audit.view", N_("View the audit log")),
        Permission("settings.manage", N_("Change application settings")),
        Permission("system.view", N_("See version, queue and health status")),
    ),
    menu=(
        MenuItem(N_("Users"), "/admin/users", "users.manage", order=10, section=ADMIN),
        MenuItem(N_("Roles"), "/admin/roles", "roles.manage", order=20, section=ADMIN),
        MenuItem(N_("AD groups"), "/admin/ad-groups", "roles.manage", order=30, section=ADMIN),
        MenuItem(N_("System"), "/admin/system", "system.view", order=90, section=ADMIN),
    ),
)
