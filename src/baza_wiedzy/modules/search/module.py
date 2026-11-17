"""Manifest of the search module: full-text index, queries, snippets."""

from baza_wiedzy.core.registry import MenuItem, ModuleManifest
from baza_wiedzy.core.web.i18n import N_

manifest = ModuleManifest(
    name="search",
    version="0.0.0",
    requires=("documents",),
    # Hidden until the pages exist (Phase 3).
    # Hidden until the pages exist (Phase 3).
    menu=(
        MenuItem(
            label=N_("Search"),
            path="/search",
            permission="documents.read",
            order=10,
            available=False,
        ),
    ),
    migrations_branch="search",
    migrations_path="baza_wiedzy.modules.search.migrations",
)
