"""Manifest of the core (always enabled)."""

from baza_wiedzy.core.registry import ModuleManifest, Permission

manifest = ModuleManifest(
    name="core",
    version="0.0.0",
    migrations_branch="core",
    migrations_path="baza_wiedzy.core.db.migrations",
    permissions=(
        Permission("users.manage", "Manage users, assign roles, revoke sessions"),
        Permission("roles.manage", "Create and edit roles, map AD groups to roles"),
        Permission("audit.view", "View the audit log"),
        Permission("settings.manage", "Change application settings"),
        Permission("system.view", "See version, queue and health status"),
    ),
)
