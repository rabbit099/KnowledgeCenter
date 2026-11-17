"""Manifest of the documents module: documents, versions, categories, lifecycle."""

from baza_wiedzy.core.registry import MenuItem, ModuleManifest, Permission
from baza_wiedzy.core.web.i18n import N_

manifest = ModuleManifest(
    name="documents",
    version="0.0.0",
    permissions=(
        Permission("documents.read", N_("Search, view and download published documents")),
        Permission(
            "documents.upload",
            N_("Upload documents and new versions (they start as drafts)"),
        ),
        Permission("documents.publish", N_("Publish and unpublish own documents")),
        Permission(
            "documents.publish_any",
            N_("Publish and unpublish documents uploaded by others"),
        ),
        Permission("documents.read_drafts", N_("See and find all drafts")),
        Permission(
            "documents.edit_any",
            N_("Edit metadata, archive and restore documents of others"),
        ),
        Permission("documents.delete", N_("Hard-delete documents (GDPR/RODO erasure)")),
        Permission("categories.manage", N_("Create, rename, delete categories")),
    ),
    # Hidden until the pages exist (Phase 2).
    # Hidden until the pages exist (Phase 2).
    menu=(
        MenuItem(
            label=N_("Documents"),
            path="/documents",
            permission="documents.read",
            order=20,
            available=False,
        ),
    ),
    migrations_branch="documents",
    migrations_path="baza_wiedzy.modules.documents.migrations",
)
