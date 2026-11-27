"""Local relevance check with your own documents and queries: `make relevance` (never in CI).

THE REPOSITORY IS PUBLIC. The documents and the query file stay outside it: `make relevance`
refuses paths inside the repository, and typical names are in .gitignore. This tool only talks
to the LOCAL development stack (`make up`): it uploads every file of the directory as a DRAFT of
the given user (seen only by that user and holders of documents.read_drafts), waits until the
worker has processed them and OCR (ocr-worker) has recognised their scans, runs every query of
the file as that user and prints the first results (score, title, where the hit is, "(OCR)"
when the hit is in text recognised by OCR, a fragment) for a manual rating. Large scans wait
"on hold" like in the application; `--start-ocr` (make relevance ... OCR=1) starts them,
audited as document.ocr_request by that user. Files already uploaded
by that user (same SHA-256) are not uploaded again. Output goes to the terminal only, never to a
log; do not paste it into issues, commits or chats outside the company.

Query file: UTF-8, one query per line; empty lines and lines starting with # are ignored.
Optionally `query => expected` where expected is a part of the title or file name that should
be among the first results; the line then ends with OK or MISSING.

Runs inside the app container of the development stack (see the Makefile):
  python /scripts/relevance.py --docs /relevance/docs --queries /relevance/queries.txt --user NAME
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import func, select

from baza_wiedzy.core.audit.service import Actor
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.models import User
from baza_wiedzy.core.db.session import make_engine, make_session_factory
from baza_wiedzy.core.permissions.service import current_user
from baza_wiedzy.core.queue import setup_broker
from baza_wiedzy.core.registry import load_registry
from baza_wiedzy.modules.documents import upload
from baza_wiedzy.modules.documents.filetypes import UnsupportedFile
from baza_wiedzy.modules.documents.models import (
    DONE,
    FAILED,
    NEEDS_OCR,
    OCR_ON_HOLD,
    OCR_QUEUED,
    OCR_RUNNING,
    Document,
    Version,
)
from baza_wiedzy.modules.documents.storage import LocalStorage
from baza_wiedzy.modules.ingestion.services import api as ingestion
from baza_wiedzy.modules.ingestion.tasks import enqueue, enqueue_ocr
from baza_wiedzy.modules.search.backend import QueryProblem, SearchRequest, SearchTimeout
from baza_wiedzy.modules.search.postgres import PostgresBackend

FINAL = (DONE, NEEDS_OCR, FAILED)
OCR_BUSY = (OCR_QUEUED, OCR_RUNNING)


def _state(processing: str, ocr: str) -> str:
    """One label per version for the progress line: "needs_ocr/queued" while OCR waits."""
    return f"{processing}/{ocr}" if processing == NEEDS_OCR else processing


def _finished(processing: str, ocr: str) -> bool:
    return processing in FINAL and not (processing == NEEDS_OCR and ocr in OCR_BUSY)


def _plain(snippet: str) -> str:
    """The HTML snippet as terminal text: hits in «», entities decoded."""
    text = html.unescape(snippet.replace("<mark>", "«").replace("</mark>", "»"))
    return " ".join(text.split())


async def _chunks(path: Path) -> AsyncIterator[bytes]:
    with path.open("rb") as handle:
        while chunk := handle.read(upload.CHUNK):
            yield chunk


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _where(origin: list[dict[str, object]], page_no: int | None) -> str:
    parts = []
    for segment in origin:
        if segment.get("type") == "attachment":
            parts.append(f"załącznik: {segment.get('name')}")
        elif segment.get("type") == "page":
            parts.append(f"strona {segment.get('no')}")
    if page_no and not any(s.get("type") == "page" for s in origin):
        parts.append(f"strona {page_no}")
    return " \u203a ".join(parts) or "treść"  # the separator of the UI


def _queries(path: Path) -> list[tuple[str, str | None]]:
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        query, _, expected = line.partition("=>")
        result.append((query.strip(), expected.strip() or None))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--docs", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument(
        "--user", required=True, help="user name of the uploading/searching account"
    )
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument(
        "--wait", type=int, default=1800, help="seconds to wait for processing and OCR"
    )
    parser.add_argument(
        "--start-ocr",
        action="store_true",
        help="start OCR of large scans of the set that wait on hold (audited)",
    )
    parser.add_argument(
        "--only-uploaded",
        action="store_true",
        help="show only documents of the directory (hide other documents the user can see)",
    )
    args = parser.parse_args()

    settings = Settings()
    registry = load_registry(settings.enabled_modules)
    if "search" not in registry:
        print("The search module is not enabled (ENABLED_MODULES).", file=sys.stderr)
        return 1
    setup_broker(settings, registry)
    catalog = frozenset(p.code for p in registry.permissions())
    engine = make_engine(settings)
    factory = make_session_factory(engine)
    storage = LocalStorage(settings.storage_dir)
    with factory() as db:
        account = db.scalar(select(User).where(func.lower(User.username) == args.user.lower()))
        if account is None:
            print(
                f"No user {args.user!r}: log in once or create the account first.", file=sys.stderr
            )
            return 1
        user = current_user(db, account, catalog)
    if not user.can("documents.upload") or not user.can("documents.read"):
        print("The user needs documents.upload and documents.read (e.g. the Publisher role).")
        return 1

    files = sorted(p for p in args.docs.rglob("*") if p.is_file() and not p.name.startswith("."))
    print(f"==> {len(files)} file(s); uploading new ones as drafts of {user.username}")
    actor = Actor(user.id, user.username)
    waiting: list[object] = []
    reused = skipped = 0
    max_bytes = settings.max_upload_mb * 1024 * 1024
    for path in files:
        sha = _sha256(path)
        with factory() as db:
            known = db.scalar(
                select(Version.id)
                .join(Document, Document.id == Version.document_id)
                .where(Version.sha256 == sha, Document.owner_id == user.id)
            )
        if known is not None:
            reused += 1
            waiting.append(known)
            continue
        try:
            received = asyncio.run(upload.receive(_chunks(path), storage, max_bytes))
            with factory.begin() as db:
                done = upload.create(
                    db, storage, received, actor=actor, raw_filename=path.name, title="", ip=None
                )
                version_id = done.version.id
        except UnsupportedFile as exc:
            skipped += 1
            print(f"    skipped {path.name}: {exc.message}")
            continue
        enqueue(version_id, settings)
        waiting.append(version_id)
    print(f"    uploaded {len(waiting) - reused}, already there {reused}, refused {skipped}")

    if args.start_ocr:
        started_ocr = 0
        with factory.begin() as db:
            on_hold = db.scalars(
                select(Version.id).where(Version.id.in_(waiting), Version.ocr_status == OCR_ON_HOLD)
            ).all()
            for version_id in on_hold:
                started_ocr += ingestion.request_ocr(
                    db, version_id, actor=actor, ip=None, source="relevance"
                )
        if started_ocr:
            enqueue_ocr(settings, count=started_ocr)
        print(f"    OCR started for {started_ocr} large scan(s) on hold")

    deadline = time.monotonic() + args.wait
    while True:
        with factory() as db:
            rows = db.execute(
                select(Version.processing_status, Version.ocr_status).where(Version.id.in_(waiting))
            ).all()
        statuses: dict[str, int] = {}
        for processing, ocr in rows:
            label = _state(processing, ocr)
            statuses[label] = statuses.get(label, 0) + 1
        unfinished = sum(1 for processing, ocr in rows if not _finished(processing, ocr))
        print(f"    processing: {dict(sorted(statuses.items()))}", flush=True)
        if not unfinished or time.monotonic() > deadline:
            break
        time.sleep(5)
    if unfinished:
        print(
            "    not everything was processed in time; are worker and ocr-worker running "
            "(make logs)? Large scans: OCR takes about 2 s per page."
        )
    held = statuses.get(f"{NEEDS_OCR}/{OCR_ON_HOLD}", 0)
    if held and not args.start_ocr:
        print(
            f"    {held} large scan(s) wait on hold (more than OCR_AUTO_MAX_PAGES pages): their "
            "scanned pages are not searchable yet; run again with OCR=1 to start them"
        )

    with factory() as db:
        mine = set(db.scalars(select(Version.document_id).where(Version.id.in_(waiting))))
    backend = PostgresBackend(settings)
    ok = missing = 0
    for number, (query, expected) in enumerate(_queries(args.queries), start=1):
        with factory() as db:
            started = time.perf_counter()
            try:
                page = backend.search(db, user, SearchRequest(query=query))
                hits = list(page.hits)
                if args.only_uploaded:
                    # Count only documents of the set: walk every result page (at most
                    # SEARCH_MAX_PAGES); the order is the ranking order.
                    for number_of_page in range(2, page.pages + 1):
                        more = backend.search(
                            db, user, SearchRequest(query=query, page=number_of_page)
                        )
                        hits.extend(more.hits)
            except (QueryProblem, SearchTimeout) as exc:
                print(f"\n[{number}] {query}\n    {type(exc).__name__}")
                continue
            ms = (time.perf_counter() - started) * 1000
        note = ", by date: too slow for ranking" if page.by_date_fallback else ""
        if args.only_uploaded:
            hits = [h for h in hits if h.document_id in mine]
            others = page.total - len(hits)
            count = f"{len(hits)} document(s) from the set, +{others} outside it"
        else:
            count = f"{page.total} document(s)"
        print(f"\n[{number}] {query}   ({count}, {ms:.0f} ms{note})")
        hits = hits[: args.top]
        for rank, hit in enumerate(hits, start=1):
            state = {"draft": " · szkic", "archived": " · zarchiwizowany"}.get(hit.status, "")
            where = _where(hit.origin, hit.page_no) if hit.snippet else "tytuł/nazwa/nagłówki"
            ocr = " (OCR)" if hit.snippet and hit.ocr_applied else ""
            print(
                f"  {rank}. {hit.score:.3f}  {hit.title}  [{hit.filename}]  — {where}{ocr}{state}"
            )
            if hit.snippet:
                print(f"       {_plain(str(hit.snippet))}")
        if expected:
            found = any(expected.lower() in (h.title + " " + h.filename).lower() for h in hits)
            ok, missing = ok + found, missing + (not found)
            print(
                f"  expected {expected!r} in the first {args.top}: {'OK' if found else 'MISSING'}"
            )
    if ok or missing:
        print(f"\n==> expectations: {ok} OK, {missing} MISSING")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
