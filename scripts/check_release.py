"""Refuse a release whose tag does not match the project (run by .github/workflows/release.yml).

Usage: python scripts/check_release.py v1.2.3

Checks: the tag has the form vX.Y.Z; `version` in pyproject.toml is X.Y.Z; both changelogs
(docs/en/changelog.md, docs/pl/changelog.md) have a section `## [X.Y.Z]`.
Exit code 0 = OK, 1 = refused (each reason is printed).
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")
CHANGELOGS = ("docs/en/changelog.md", "docs/pl/changelog.md")


def check(tag: str, root: Path) -> list[str]:
    match = TAG_RE.match(tag)
    if not match:
        return [f"tag {tag!r} is not of the form vX.Y.Z"]
    version = match.group(1)
    problems: list[str] = []
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    declared = project.get("project", {}).get("version")
    if declared != version:
        problems.append(f"pyproject.toml has version {declared!r}, the tag says {version!r}")
    for changelog in CHANGELOGS:
        lines = (root / changelog).read_text(encoding="utf-8").splitlines()
        if not any(line.startswith(f"## [{version}]") for line in lines):
            problems.append(f"{changelog} has no section '## [{version}]'")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: check_release.py vX.Y.Z", file=sys.stderr)
        return 1
    problems = check(argv[0], Path(__file__).resolve().parent.parent)
    if problems:
        print(f"Release {argv[0]} REFUSED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"Release {argv[0]} matches pyproject.toml and both changelogs.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
