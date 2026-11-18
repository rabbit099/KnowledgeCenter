"""Public interface of the search module (the only part other modules may import).

Nothing is offered to other modules yet: search reacts to document.version.extracted and reads
documents and ingestion through their public interfaces. Operators use the CLI commands
`reindex-search` and `search-check` (baza_wiedzy.cli).
"""
