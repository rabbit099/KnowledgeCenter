"""The synthetic search corpus is consistent (shared by golden tests and `make probki`)."""

from __future__ import annotations

from collections import Counter

from fixtures import search_corpus
from fixtures.search_corpus import DOCS, KEYS, QUERIES, expected_text


def test_keys_and_file_names_are_unique() -> None:
    assert len(KEYS) == len(DOCS)
    assert not [n for n, c in Counter(d.filename for d in DOCS).items() if c > 1]
    assert len({d.id for d in DOCS}) == len(DOCS)


def test_queries_name_existing_documents() -> None:
    for case in QUERIES:
        assert case.must | case.must_not <= KEYS, case.query
        assert not case.must & case.must_not, case.query
        assert case.top is None or case.top in case.must, case.query
        assert case.as_user in {"reader", search_corpus.ANNA, "admin"}
        assert case.note


def test_corpus_covers_every_state_and_the_required_cases() -> None:
    assert {d.state for d in DOCS} == {"published", "draft", "archived"}
    assert {d.filename.rsplit(".", 1)[1] for d in DOCS} >= {"txt", "docx", "pdf", "eml"}
    queries = {q.query for q in QUERIES}
    assert {"umowa", "umowie", "Łódź", "lodz", '"zasady bezpieczeństwa"'} <= queries
    assert any(q.archived for q in QUERIES) and any(q.as_user == "anna" for q in QUERIES)


def test_every_file_builds_and_is_listed_for_the_manual_test() -> None:
    text = expected_text()
    for doc in DOCS:
        data = doc.build()
        assert len(data) > 20, doc.key
        assert doc.filename in text
    for case in QUERIES:
        assert f"<{case.query}>" in text
