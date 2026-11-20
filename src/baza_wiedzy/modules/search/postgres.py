"""PostgreSQL full-text search (ADR-002, ADR-003, ADR-026, ADR-027).

One search is one transaction with `statement_timeout` (SEARCH_TIMEOUT_MS):
1. the query is parsed with websearch_to_tsquery in the three configurations; each is OR-ed
   with the identifier alternatives (identifiers.py) so "17/2026" also finds "UM/17/2026";
2. candidates: text parts (srch_parts) and metadata rows (srch_documents) of the versions in
   documents.services.api.search_scope (visibility, current version and filters in SQL);
3. ranking per document: the best part (ts_rank_cd, normalisation 32, the best of the three
   configurations; simple_unaccent x 0.8; the configuration of the other language than the
   detected one x 0.9) plus the metadata (ts_rank with weights A title/subject, B file
   name/sender/recipients; trigram near match x 0.3, never more than an exact match);
4. exclusions (-word, -"phrase") remove the whole document when any part or the metadata
   contains them;
5. count(*) OVER () gives the number of documents (ranking reads every match anyway);
6. ts_headline only for the parts of the visible page, with sentinels (snippets.py).
If ranking exceeds the time limit, the search runs again without ranking, newest first, and the
page says so; if that is too slow as well, SearchTimeout. Parameters are always bound; the
query text never reaches a log (ADR-025).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import replace
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Float,
    case,
    cast,
    exists,
    false,
    func,
    literal,
    null,
    or_,
    select,
    text,
    true,
    union,
)
from sqlalchemy.dialects.postgresql import REGCONFIG, TSQUERY, distinct_on
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.permissions.service import CurrentUser
from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.modules.documents.services import api as documents
from baza_wiedzy.modules.search.backend import (
    QueryProblem,
    SearchHit,
    SearchPage,
    SearchRequest,
    SearchTimeout,
)
from baza_wiedzy.modules.search.identifiers import fold, query_alternatives
from baza_wiedzy.modules.search.models import CONFIGS, SearchDocument, SearchPart
from baza_wiedzy.modules.search.snippets import HEADLINE_OPTIONS, to_html

log = logging.getLogger(__name__)

QUERY_CANCELED = "57014"
SIMPLE_FACTOR = 0.8
OTHER_LANGUAGE_FACTOR = 0.9
TRIGRAM_FACTOR = 0.3
_NEGATION = re.compile(r'(?:(?<=\s)|^)-("[^"]+"|[^\s"]+)')
_OPERATOR = re.compile(r'"|(?:(?<=\s)|^)-|\sor\s', re.IGNORECASE)


def normalise(query: str) -> str:
    return " ".join(query.split())


def negations(query: str) -> str:
    """'faktura -korekta -"stara cena"' -> 'korekta or "stara cena"' (empty: none)."""
    return " or ".join(m.group(1) for m in _NEGATION.finditer(query))


def _ws(config: str, value: Any) -> ColumnElement[Any]:
    return func.websearch_to_tsquery(cast(literal(config), REGCONFIG), value, type_=TSQUERY)


def _or(*queries: ColumnElement[Any]) -> ColumnElement[Any]:
    result = queries[0]
    for other in queries[1:]:
        result = result.op("||", return_type=TSQUERY)(other)
    return result


def _matches(tsv: Any, query: Any) -> ColumnElement[bool]:
    return tsv.op("@@")(query)  # type: ignore[no-any-return]


class PostgresBackend:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # --- public ------------------------------------------------------------------------

    def search(self, db: Session, user: CurrentUser, request: SearchRequest) -> SearchPage:
        if request.owner_id is not None and not documents.can_see_authors(user):
            request = replace(request, owner_id=None)  # the author filter is not theirs (ADR-030)
        query = normalise(request.query)
        settings = self.settings
        if len(query) < settings.search_min_query_chars:
            raise QueryProblem(
                N_("Type at least %(n)s characters."), n=settings.search_min_query_chars
            )
        if len(query) > settings.search_max_query_chars:
            raise QueryProblem(
                N_("The query is too long (at most %(n)s characters)."),
                n=settings.search_max_query_chars,
            )
        started = time.monotonic()
        fallback = False
        try:
            page = self._run(db, user, request, query, ranked=request.sort == "relevance")
        except OperationalError as exc:
            if getattr(exc.orig, "sqlstate", None) != QUERY_CANCELED:
                raise
            db.rollback()
            fallback = True
            try:
                page = self._run(db, user, request, query, ranked=False)
            except OperationalError as again:
                if getattr(again.orig, "sqlstate", None) != QUERY_CANCELED:
                    raise
                db.rollback()
                log.warning(
                    "search: time limit exceeded twice: filters=%s duration_ms=%d",
                    ",".join(request.filter_names()) or "-",
                    (time.monotonic() - started) * 1000,
                )
                raise SearchTimeout from None
        log.info(
            "search: results=%d page=%d sort=%s filters=%s fallback=%s duration_ms=%d",
            page.total,
            page.page,
            "date" if fallback else request.sort,
            ",".join(request.filter_names()) or "-",
            "yes" if fallback else "no",
            (time.monotonic() - started) * 1000,
        )
        if fallback:
            return SearchPage(
                hits=page.hits,
                total=page.total,
                page=page.page,
                pages=page.pages,
                size=page.size,
                by_date_fallback=True,
                notice=page.notice,
            )
        return page

    # --- internals -----------------------------------------------------------------------

    def _limits(self, db: Session) -> None:
        db.execute(
            text("SELECT set_config('statement_timeout', :ms, true)"),
            {"ms": str(self.settings.search_timeout_ms)},
        )
        db.execute(
            text("SELECT set_config('pg_trgm.word_similarity_threshold', :t, true)"),
            {"t": str(self.settings.search_trigram_threshold)},
        )
        # Hash joins and the sort of a broad query fit in memory instead of temp files.
        db.execute(text("SELECT set_config('work_mem', '16MB', true)"))

    def _run(
        self, db: Session, user: CurrentUser, request: SearchRequest, query: str, *, ranked: bool
    ) -> SearchPage:
        self._limits(db)
        alt1, alt2 = query_alternatives(query)
        # Only alternatives that differ: OR-ing a query with itself made matching and ranking
        # do the same work up to three times (stage C measurements).
        variants = list(dict.fromkeys((query, alt1, alt2)))
        q = select(
            *(_or(*(_ws(cfg, v) for v in variants)).label(key) for key, cfg in CONFIGS.items())
        ).cte("q")
        empty = db.execute(
            select(*(func.numnode(q.c[key]) for key in CONFIGS)).select_from(q)
        ).one()
        size = self.settings.search_page_size
        if not any(empty):
            return SearchPage(
                hits=[],
                total=0,
                page=1,
                pages=0,
                size=size,
                notice=N_(
                    "The query contains only very common words, which are not searched. "
                    "Add a more specific word."
                ),
            )
        scope = documents.search_scope(
            user,
            documents.ScopeFilters(
                kinds=request.kinds,
                owner_id=request.owner_id,
                since=request.since,
                before=request.before,
                include_archived=request.include_archived,
            ),
        )
        p = SearchPart
        part_match = or_(*(_matches(getattr(p, f"tsv_{key}"), q.c[key]) for key in CONFIGS))
        # Ranking reads (and decompresses) a tsvector per configuration, the main cost of a
        # broad query (stage C measurements). First the configuration of the part's language
        # (both for "unknown"); the others only when it gives nothing (an identifier, a word
        # typed without diacritics, a word of the other language): LATERAL computes it once.
        matched_query = (
            select(
                p.id.label("part_id"),
                p.version_id.label("version_id"),
                p.document_id.label("document_id"),
            )
            .select_from(p)
            .join(q, true())
        )
        if ranked:
            rank_pl = func.ts_rank_cd(p.tsv_pl, q.c.pl, 32)
            rank_en = func.ts_rank_cd(p.tsv_en, q.c.en, 32)
            own = case(
                (p.language == "en", rank_en),
                (p.language == "pl", rank_pl),
                else_=func.greatest(rank_pl, rank_en),
            )
            first = select(own.label("r")).correlate(p.__table__, q).lateral("first_rank")
            other = case((p.language == "en", rank_pl), else_=rank_en)
            score: ColumnElement[Any] = case(
                (first.c.r > 0, first.c.r),
                else_=func.greatest(
                    func.ts_rank_cd(p.tsv_simple, q.c.simple, 32) * SIMPLE_FACTOR,
                    other * OTHER_LANGUAGE_FACTOR,
                ),
            )
            matched_query = matched_query.join(first, true())
        else:
            score = literal(0.0, Float)
        matched_query = matched_query.add_columns(score.label("score")).where(part_match)
        if request.language:
            matched_query = matched_query.where(p.language == request.language)
        # Materialised and narrow: the wide rows (tsvectors) never reach the joins.
        matched = matched_query.cte("matched").prefix_with("MATERIALIZED")
        content = (
            select(
                matched.c.document_id,
                matched.c.part_id,
                scope.c.version_id.label("version_id"),
                scope.c.doc_date.label("doc_date"),
                matched.c.score,
            )
            .select_from(matched)
            .join(scope, scope.c.version_id == matched.c.version_id)
            .cte("content")
        )
        best = (
            select(content)
            .ext(distinct_on(content.c.document_id))
            .order_by(content.c.document_id, content.c.score.desc(), content.c.part_id)
            .cte("best")
        )
        m = SearchDocument
        folded = fold(query)
        # Near matches are for words (typos in titles); numbers and codes must match exactly,
        # or "FV/2026/11/0042" would also find "FV/2026/11/0043".
        use_trigram = (
            len(folded) >= 4
            and not _OPERATOR.search(query)
            and not any(ch.isdigit() for ch in folded)
        )
        meta_match = or_(
            *(_matches(getattr(m, f"tsv_meta_{key}"), q.c[key]) for key in CONFIGS),
            literal(folded).op("<%")(m.meta_fold) if use_trigram else false(),
        )
        if ranked:
            meta_score: ColumnElement[Any] = func.greatest(
                func.ts_rank(m.tsv_meta_pl, q.c.pl, 32),
                func.ts_rank(m.tsv_meta_en, q.c.en, 32),
                func.ts_rank(m.tsv_meta_simple, q.c.simple, 32) * SIMPLE_FACTOR,
                (func.word_similarity(folded, m.meta_fold) * TRIGRAM_FACTOR)
                if use_trigram
                else literal(0.0, Float),
            )
        else:
            meta_score = literal(0.0, Float)
        meta = (
            select(
                m.document_id.label("document_id"),
                scope.c.version_id.label("version_id"),
                scope.c.doc_date.label("doc_date"),
                meta_score.label("score"),
            )
            .select_from(m)
            .join(scope, scope.c.version_id == m.version_id)
            .join(q, true())
            .where(meta_match, true() if not request.language else false())
            .cte("meta")
        )
        document_id = func.coalesce(best.c.document_id, meta.c.document_id)
        version_id = func.coalesce(best.c.version_id, meta.c.version_id)
        doc_date = func.coalesce(best.c.doc_date, meta.c.doc_date)
        total_score = func.coalesce(best.c.score, 0.0) + func.coalesce(meta.c.score, 0.0)
        docs = select(
            document_id.label("document_id"),
            version_id.label("version_id"),
            doc_date.label("doc_date"),
            total_score.label("score"),
            best.c.part_id.label("part_id"),
        ).select_from(best.join(meta, best.c.document_id == meta.c.document_id, full=True))
        negated = negations(query)
        if negated:
            neg = select(*(_ws(cfg, negated).label(key) for key, cfg in CONFIGS.items())).cte("neg")
            # One pass over the GIN indexes gives every version containing an excluded word;
            # checking each candidate's parts instead cost a second per broad query.
            p2, m2 = SearchPart.__table__.alias("p2"), SearchDocument.__table__.alias("m2")
            excluded = union(
                select(p2.c.version_id)
                .select_from(p2.join(neg, true()))
                .where(or_(*(_matches(p2.c[f"tsv_{key}"], neg.c[key]) for key in CONFIGS))),
                select(m2.c.version_id)
                .select_from(m2.join(neg, true()))
                .where(or_(*(_matches(m2.c[f"tsv_meta_{key}"], neg.c[key]) for key in CONFIGS))),
            ).cte("excluded")
            docs = docs.where(~exists(select(1).where(excluded.c.version_id == version_id)))
        ranked_docs = docs.cte("docs")
        order = (
            [ranked_docs.c.score.desc(), ranked_docs.c.doc_date.desc()]
            if ranked
            else [ranked_docs.c.doc_date.desc()]
        )
        max_pages = self.settings.search_max_pages
        page_no = max(1, min(request.page, max_pages))
        rows = list(
            db.execute(
                select(
                    ranked_docs.c.document_id,
                    ranked_docs.c.part_id,
                    ranked_docs.c.score,
                    func.count().over().label("total"),
                )
                .order_by(*order, ranked_docs.c.document_id)
                .limit(size)
                .offset((page_no - 1) * size)
            ).all()
        )
        total = int(rows[0].total) if rows else self._count_if_beyond(db, ranked_docs, page_no)
        pages = min(max_pages, -(-total // size)) if total else 0
        hits = self._details(db, user, request, q, rows) if rows else []
        return SearchPage(hits=hits, total=total, page=page_no, pages=pages, size=size)

    def _count_if_beyond(self, db: Session, docs: Any, page_no: int) -> int:
        """No row on this page: the total (for a page number past the end), or 0."""
        if page_no == 1:
            return 0
        return int(db.scalar(select(func.count()).select_from(docs)) or 0)

    def _details(
        self, db: Session, user: CurrentUser, request: SearchRequest, q: Any, rows: list[Any]
    ) -> list[SearchHit]:
        show_authors = documents.can_see_authors(user)
        ids = [row.document_id for row in rows]
        scope = documents.search_scope(
            user, documents.ScopeFilters(include_archived=request.include_archived)
        )
        info = {
            r.document_id: r for r in db.execute(select(scope).where(scope.c.document_id.in_(ids)))
        }
        part_ids = [row.part_id for row in rows if row.part_id is not None]
        parts: dict[int, Any] = {}
        if part_ids:
            p = SearchPart
            headline = case(
                *(
                    (
                        _matches(getattr(p, f"tsv_{key}"), q.c[key]),
                        func.ts_headline(
                            cast(literal(cfg), REGCONFIG), p.text_content, q.c[key],
                            HEADLINE_OPTIONS,
                        ),
                    )
                    for key, cfg in CONFIGS.items()
                ),
                else_=null(),
            )  # fmt: skip
            parts = {
                r.id: r
                for r in db.execute(
                    select(
                        p.id, p.origin, p.page_no, p.chunk_no, p.language, headline.label("snippet")
                    )
                    .select_from(p)
                    .join(q, true())
                    .where(p.id.in_(part_ids))
                )
            }
        hits = []
        for row in rows:
            doc = info.get(row.document_id)
            if doc is None:  # changed between the two statements (e.g. archived just now)
                continue
            part = parts.get(row.part_id) if row.part_id is not None else None
            hits.append(
                SearchHit(
                    document_id=doc.document_id,
                    title=doc.title,
                    status=doc.status,
                    kind=doc.kind,
                    filename=doc.filename,
                    owner_name=doc.owner_name if show_authors else None,
                    doc_date=doc.doc_date,
                    score=float(row.score or 0.0),
                    snippet=to_html(part.snippet) if part is not None and part.snippet else None,
                    origin=list(part.origin) if part is not None else [],
                    page_no=part.page_no if part is not None else None,
                    chunk_no=part.chunk_no if part is not None else 1,
                    language=part.language if part is not None else None,
                )
            )
        return hits
