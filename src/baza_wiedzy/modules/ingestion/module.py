"""Manifest of the ingestion module: upload processing, extractors, OCR (worker side)."""

from baza_wiedzy.core.registry import ModuleManifest

manifest = ModuleManifest(
    name="ingestion",
    version="0.0.0",
    requires=("documents",),
    migrations_branch="ingestion",
)
