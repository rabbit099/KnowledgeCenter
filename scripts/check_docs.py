"""Verify the consistency of the bilingual documentation (CLAUDE.md section 9).

Rules:
- docs/en and docs/pl contain the same set of files (also in subdirectories such as modules/);
- every file pair has the same number of H2 and H3 headings (code fences are ignored);
- every file has the header line (`> Applies to: ...` / `> Dotyczy: ...`) as its first block;
- both changelogs contain an `[Unreleased]` section.

Exit code 0 = OK, 1 = problems found (each one is printed).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LANGS = {
    "en": re.compile(r"^> Applies to: .+ · Last reviewed: \d{4}-\d{2}-\d{2}\s*$"),
    "pl": re.compile(r"^> Dotyczy: .+ · Ostatnia aktualizacja: \d{4}-\d{2}-\d{2}\s*$"),
}
UNRELEASED = "## [Unreleased]"


def doc_files(root: Path) -> set[Path]:
    return {p.relative_to(root) for p in root.rglob("*.md")}


def headings(text: str) -> tuple[int, int]:
    """Return (number of H2, number of H3), skipping fenced code blocks."""
    h2 = h3 = 0
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("## "):
            h2 += 1
        elif line.startswith("### "):
            h3 += 1
    return h2, h3


def header_line_ok(text: str, pattern: re.Pattern[str]) -> bool:
    """The header line is the first non-empty line after the H1 title."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return len(lines) >= 2 and lines[0].startswith("# ") and bool(pattern.match(lines[1]))


def check(docs_dir: Path) -> list[str]:
    problems: list[str] = []
    roots = {lang: docs_dir / lang for lang in LANGS}
    for lang, root in roots.items():
        if not root.is_dir():
            return [f"missing directory: {root}"]
        _ = lang
    files = {lang: doc_files(root) for lang, root in roots.items()}

    for missing in sorted(files["en"] - files["pl"]):
        problems.append(f"missing in pl/: {missing.as_posix()}")
    for missing in sorted(files["pl"] - files["en"]):
        problems.append(f"missing in en/: {missing.as_posix()}")

    for lang, pattern in LANGS.items():
        for rel in sorted(files[lang]):
            text = (roots[lang] / rel).read_text(encoding="utf-8")
            if not header_line_ok(text, pattern):
                problems.append(f"{lang}/{rel.as_posix()}: missing or malformed header line")

    for rel in sorted(files["en"] & files["pl"]):
        counts = {lang: headings((roots[lang] / rel).read_text(encoding="utf-8")) for lang in LANGS}
        if counts["en"] != counts["pl"]:
            problems.append(
                f"{rel.as_posix()}: heading count differs "
                f"(en H2/H3 = {counts['en']}, pl H2/H3 = {counts['pl']})"
            )

    for lang, root in roots.items():
        changelog = root / "changelog.md"
        if not changelog.is_file():
            problems.append(f"{lang}/changelog.md is missing")
        elif UNRELEASED not in changelog.read_text(encoding="utf-8").splitlines():
            problems.append(f"{lang}/changelog.md: no '{UNRELEASED}' section")
    return problems


def main() -> int:
    docs_dir = Path(__file__).resolve().parent.parent / "docs"
    problems = check(docs_dir)
    if problems:
        print("Documentation check FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Documentation check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
