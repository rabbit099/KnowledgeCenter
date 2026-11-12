from pathlib import Path

import check_docs

EN_HEADER = "> Applies to: v1 · Last reviewed: 2026-01-01"
PL_HEADER = "> Dotyczy: v1 · Ostatnia aktualizacja: 2026-01-01"


def write(path: Path, header: str, body: str = "## A\n\n### B\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Title\n\n{header}\n\n{body}", encoding="utf-8")


def make_docs(tmp_path: Path) -> Path:
    for lang, header in (("en", EN_HEADER), ("pl", PL_HEADER)):
        write(tmp_path / lang / "a.md", header)
        write(tmp_path / lang / "modules" / "m.md", header)
        write(tmp_path / lang / "changelog.md", header, "## [Unreleased]\n")
    return tmp_path


def test_valid_docs_pass(tmp_path: Path) -> None:
    assert check_docs.check(make_docs(tmp_path)) == []


def test_missing_file_in_subdirectory(tmp_path: Path) -> None:
    docs = make_docs(tmp_path)
    (docs / "pl" / "modules" / "m.md").unlink()
    assert check_docs.check(docs) == ["missing in pl/: modules/m.md"]


def test_heading_count_mismatch(tmp_path: Path) -> None:
    docs = make_docs(tmp_path)
    write(docs / "pl" / "a.md", PL_HEADER, "## A\n")
    problems = check_docs.check(docs)
    assert len(problems) == 1
    assert "a.md" in problems[0]
    assert "heading count differs" in problems[0]


def test_headings_in_code_fence_are_ignored() -> None:
    assert check_docs.headings("## A\n```\n## not a heading\n```\n### B\n") == (1, 1)


def test_missing_header_line(tmp_path: Path) -> None:
    docs = make_docs(tmp_path)
    write(docs / "en" / "a.md", "Some text")
    assert any("en/a.md" in p and "header" in p for p in check_docs.check(docs))


def test_wrong_language_header(tmp_path: Path) -> None:
    docs = make_docs(tmp_path)
    write(docs / "pl" / "a.md", EN_HEADER)
    assert any("pl/a.md" in p for p in check_docs.check(docs))


def test_missing_unreleased(tmp_path: Path) -> None:
    docs = make_docs(tmp_path)
    write(docs / "pl" / "changelog.md", PL_HEADER, "## [1.0.0]\n")
    assert any("Unreleased" in p for p in check_docs.check(docs))


def make_modules(tmp_path: Path, *names: str) -> Path:
    modules = tmp_path / "modules_src"
    for name in names:
        (modules / name).mkdir(parents=True)
        (modules / name / "__init__.py").write_text("", encoding="utf-8")
    (modules / "__pycache__").mkdir(parents=True, exist_ok=True)
    return modules


def test_documented_module_passes(tmp_path: Path) -> None:
    docs = make_docs(tmp_path)
    assert check_docs.check(docs, make_modules(tmp_path, "m")) == []


def test_undocumented_module_fails_in_both_languages(tmp_path: Path) -> None:
    docs = make_docs(tmp_path)
    problems = check_docs.check(docs, make_modules(tmp_path, "m", "billing"))
    assert problems == [
        "module 'billing' has no documentation: en/modules/billing.md",
        "module 'billing' has no documentation: pl/modules/billing.md",
    ]


def test_directory_without_init_is_not_a_module(tmp_path: Path) -> None:
    docs = make_docs(tmp_path)
    modules = make_modules(tmp_path, "m")
    (modules / "notes").mkdir()
    assert check_docs.check(docs, modules) == []


def test_repository_modules_are_documented() -> None:
    repo = Path(check_docs.__file__).resolve().parent.parent
    assert check_docs.check(repo / "docs", repo / "src" / "baza_wiedzy" / "modules") == []
