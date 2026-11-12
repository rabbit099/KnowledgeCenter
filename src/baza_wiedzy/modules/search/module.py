"""Manifest of the search module: full-text index, queries, snippets."""

from baza_wiedzy.core.registry import MenuItem, ModuleManifest

manifest = ModuleManifest(
    name="search",
    version="0.0.0",
    requires=("documents",),
    menu=(MenuItem(label="Search", path="/search", permission="documents.read", order=10),),
    migrations_branch="search",
    migrations_path="baza_wiedzy.modules.search.migrations",
)
