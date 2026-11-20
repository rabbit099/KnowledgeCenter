"""Exposure to soupsieve (Dependabot alert #2, GHSA-mmhr-3c5m-f64f, ADR-015).

The vulnerability is in CSS selector matching (`soupsieve.compile`, bs4 `.select()`,
`.select_one()`, `.css`). It only matters when a selector runs on untrusted HTML. soupsieve is
in the dependency tree only through beautifulsoup4 <- extract-msg (extra `worker`). This file
writes down the assessment and fails as soon as it stops being true:
1. our code never imports bs4 or soupsieve;
2. extract-msg and RTFDE (which parse the HTML of MSG files) never use selectors;
3. the dependencies of the web application image (no extra) contain neither bs4 nor soupsieve
   (computed from uv.lock: the app Dockerfile runs `uv sync --frozen --no-dev` without
   `--extra worker`; `make smoke` checks the built image as well).
Even so, soupsieve is kept on a fixed version (>= 4.0.0) via exclude-newer-package.
"""

from __future__ import annotations

import ast
import importlib.util
import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SELECTOR_ATTRIBUTES = {"select", "select_one", "css"}
SELECTOR_MODULES = {"soupsieve", "bs4.css"}


def _python_files(package: str) -> list[Path]:
    spec = importlib.util.find_spec(package)
    if spec is None or spec.origin is None:
        pytest.skip(f"{package} is not installed (extra `worker`)")
    return sorted(Path(spec.origin).parent.rglob("*.py"))


def _selector_uses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in SELECTOR_ATTRIBUTES:
            found.append(f"{path.name}:{node.lineno} .{node.attr}")
        elif isinstance(node, ast.Import):
            found += [
                f"{path.name}:{node.lineno} import {a.name}"
                for a in node.names
                if a.name.split(".")[0] == "soupsieve" or a.name in SELECTOR_MODULES
            ]
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module.split(".")[0] == "soupsieve" or node.module in SELECTOR_MODULES)
        ):
            found.append(f"{path.name}:{node.lineno} from {node.module}")
    return found


def test_our_code_never_imports_bs4_or_soupsieve() -> None:
    offenders = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            offenders += [
                f"{path.relative_to(ROOT)}:{node.lineno} {name}"
                for name in names
                if name.split(".")[0] in {"bs4", "soupsieve"}
            ]
    assert not offenders, offenders


@pytest.mark.parametrize("package", ["extract_msg", "RTFDE"])
def test_msg_libraries_never_use_css_selectors(package: str) -> None:
    uses = [use for path in _python_files(package) for use in _selector_uses(path)]
    assert not uses, f"{package} now uses CSS selectors (soupsieve is reachable): {uses}"


def test_the_detector_finds_selector_use(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import soupsieve\nfrom bs4 import BeautifulSoup\n"
        "BeautifulSoup('', 'html.parser').select('div div')\nsoup.css.select('a')\n"
    )
    uses = _selector_uses(sample)
    assert any("import soupsieve" in u for u in uses)
    assert any(".select" in u for u in uses) and any(".css" in u for u in uses)


def _closure(lock: dict[str, Any], roots: list[str]) -> set[str]:
    seen: set[str] = set()
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        package = lock.get(name, {})
        todo += [str(d["name"]) for d in package.get("dependencies", [])]
    return seen


def test_web_application_dependencies_contain_neither_bs4_nor_soupsieve() -> None:
    data = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    lock = {str(p["name"]): p for p in data["package"]}
    project = lock["baza-wiedzy"]
    app = _closure(lock, [str(d["name"]) for d in project["dependencies"]])
    worker = _closure(lock, [str(d["name"]) for d in project["optional-dependencies"]["worker"]])
    assert {"beautifulsoup4", "soupsieve"}.isdisjoint(app)
    assert {"beautifulsoup4", "soupsieve"} <= worker  # the assessment above is about this path
    version = tuple(int(x) for x in str(lock["soupsieve"]["version"]).split(".")[:2])
    assert version >= (4, 0), "soupsieve must stay >= 4.0.0 (GHSA-mmhr-3c5m-f64f)"
