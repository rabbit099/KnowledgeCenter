"""Keeping the index in step with ingestion (ADR-020, ADR-026).

- `index_version` replaces all index rows of one version in the caller's transaction, under an
  advisory lock of that version (two deliveries of the same event never interleave). Parts
  without text are skipped (a scanned page before OCR). Idempotent: the event
  document.version.extracted is delivered at least once.
- `on_extracted` is the event handler (worker and CLI; the event comes from the ingestion
  outbox, so a failure here leaves the event undelivered and it is delivered again).
- `index_missing` indexes every extracted version that has no index rows (worker start, CLI).
- `check` compares the index with ingestion (numbers of parts with text) and lists the
  differences by identifier only.

Nothing here logs text, titles or file names.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from baza_wiedzy.core.runtime import Runtime
from baza_wiedzy.modules.documents.services import api as documents
from baza_wiedzy.modules.ingestion.services import api as ingestion
from baza_wiedzy.modules.search.identifiers import fold, index_terms
from baza_wiedzy.modules.search.models import SearchDocument, SearchPart
from baza_wiedzy.modules.search.snippets import strip_sentinels

log = logging.getLogger(__name__)

_FILENAME_SEPARATORS = re.compile(r"[._\-()\[\]]+")


def _filename_words(name: str) -> str:
    """ "plan-ewakuacji_budynek-B.pdf" -> "plan ewakuacji budynek B pdf" (the text parser
    would keep the whole name as one token)."""
    return " ".join(_FILENAME_SEPARATORS.sub(" ", name).split())


def index_version(db: Session, version_id: uuid.UUID) -> int | None:
    """Replace the rows of a version; number of indexed parts, None if the version is gone."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"srch:{version_id}"})
    meta = documents.index_metadata(db, version_id)
    db.execute(delete(SearchPart).where(SearchPart.version_id == version_id))
    db.execute(delete(SearchDocument).where(SearchDocument.version_id == version_id))
    if meta is None:
        return None
    count = 0
    for part in ingestion.parts_of(db, version_id):
        content = strip_sentinels(part.text)
        if not content.strip():
            continue
        db.add(
            SearchPart(
                version_id=version_id,
                document_id=meta.document_id,
                seq=part.seq,
                part_type=part.part_type,
                origin=part.origin,
                page_no=part.page_no,
                chunk_no=part.chunk_no,
                language=part.language,
                text_content=content,
                ident_terms=index_terms(content),
            )
        )
        count += 1
    meta_a = " ".join(v for v in (meta.title, meta.subject) if v)
    meta_b = " ".join(
        v for v in (_filename_words(meta.filename), meta.sender, meta.recipients) if v
    )
    db.add(
        SearchDocument(
            version_id=version_id,
            document_id=meta.document_id,
            meta_a=strip_sentinels(meta_a),
            meta_b=strip_sentinels(meta_b),
            ident_terms=index_terms(f"{meta_a} {meta.filename} {meta_b}"),
            meta_fold=fold(f"{meta_a} {meta_b}"),
        )
    )
    db.flush()
    return count


def on_extracted(payload: Mapping[str, Any]) -> None:
    from baza_wiedzy.core import runtime

    version_id = uuid.UUID(str(payload["version_id"]))
    with runtime.current().session_factory.begin() as db:
        count = index_version(db, version_id)
    log.info("search: indexed version=%s parts=%s", version_id, count)


def indexed_counts(db: Session) -> dict[uuid.UUID, int]:
    """Versions with a metadata row -> number of indexed parts."""
    rows = db.execute(
        select(SearchDocument.version_id, func.count(SearchPart.id))
        .outerjoin(SearchPart, SearchPart.version_id == SearchDocument.version_id)
        .group_by(SearchDocument.version_id)
    )
    return {row[0]: int(row[1]) for row in rows}


@dataclass
class CheckResult:
    versions: int = 0
    missing: list[uuid.UUID] = field(default_factory=list)  # extracted, not indexed
    different: list[uuid.UUID] = field(default_factory=list)  # numbers of parts differ
    orphaned: list[uuid.UUID] = field(default_factory=list)  # indexed, no longer extracted

    @property
    def ok(self) -> bool:
        return not (self.missing or self.different or self.orphaned)


def check(db: Session) -> CheckResult:
    expected = ingestion.text_part_counts(db)
    indexed = indexed_counts(db)
    result = CheckResult(versions=len(expected))
    for version_id, count in expected.items():
        if version_id not in indexed:
            result.missing.append(version_id)
        elif indexed[version_id] != count:
            result.different.append(version_id)
    result.orphaned = [v for v in indexed if v not in expected]
    return result


def index_many(factory: sessionmaker[Session], version_ids: list[uuid.UUID]) -> int:
    """Index versions one transaction each; returns how many were indexed."""
    done = 0
    for version_id in version_ids:
        with factory.begin() as db:
            if index_version(db, version_id) is not None:
                done += 1
    return done


def remove_orphans(factory: sessionmaker[Session], version_ids: list[uuid.UUID]) -> None:
    with factory.begin() as db:
        db.execute(delete(SearchPart).where(SearchPart.version_id.in_(version_ids)))
        db.execute(delete(SearchDocument).where(SearchDocument.version_id.in_(version_ids)))


def index_missing(factory: sessionmaker[Session]) -> int:
    with factory() as db:
        missing = check(db).missing
    return index_many(factory, missing)


def on_worker_start(rt: Runtime) -> None:
    """Startup hook: index what is missing (a new installation of search, lost events)."""
    indexed = index_missing(rt.session_factory)
    if indexed:
        log.info("search: indexed %s version(s) missing from the index", indexed)
