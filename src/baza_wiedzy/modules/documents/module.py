"""Manifest of the documents module: documents, versions, categories, lifecycle."""

from baza_wiedzy.core.registry import MenuItem, ModuleManifest, Permission

manifest = ModuleManifest(
    name="documents",
    version="0.0.0",
    permissions=(
        Permission("documents.read", "Search, view and download published documents"),
        Permission("documents.upload", "Upload documents and new versions (they start as drafts)"),
        Permission("documents.publish", "Publish and unpublish own documents"),
        Permission("documents.publish_any", "Publish and unpublish documents uploaded by others"),
        Permission("documents.read_drafts", "See and find all drafts"),
        Permission("documents.edit_any", "Edit metadata, archive and restore documents of others"),
        Permission("documents.delete", "Hard-delete documents (GDPR/RODO erasure)"),
        Permission("categories.manage", "Create, rename, delete categories"),
    ),
    menu=(MenuItem(label="Documents", path="/documents", permission="documents.read", order=20),),
    migrations_branch="documents",
)
