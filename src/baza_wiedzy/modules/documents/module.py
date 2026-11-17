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
    menu=(
        MenuItem(
            label=N_("My documents"),
            path="/documents",
            permission="documents.upload",
            order=20,
        ),
    ),
    routers=("baza_wiedzy.modules.documents.routes:router",),
    templates="baza_wiedzy.modules.documents",
    migrations_branch="documents",
    migrations_path="baza_wiedzy.modules.documents.migrations",
)
