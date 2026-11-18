"""Repository rules that broke CI once (Phase 0): checked from the git index, not the disk."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _git_index_modes(pattern: str) -> dict[str, str]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    output = subprocess.run(
        ["git", "ls-files", "--stage", "--", pattern],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    modes: dict[str, str] = {}
    for line in output.splitlines():
        meta, _, path = line.partition("\t")
        modes[path] = meta.split()[0]
    return modes


def test_shell_scripts_are_executable_in_git() -> None:
    # A script committed as 100644 runs locally (the disk keeps +x) but fails in CI with
    # "Permission denied". Fix with: git update-index --chmod=+x scripts/<name>.sh
    modes = _git_index_modes("scripts/*.sh")
    assert modes, "no scripts/*.sh found in the git index"
    wrong = {path: mode for path, mode in modes.items() if mode != "100755"}
    assert not wrong, f"not executable in git (expected 100755): {wrong}"


def test_env_example_lists_every_setting() -> None:
    from baza_wiedzy.core.config import Settings

    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    listed = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", text, re.MULTILINE))
    # Built in docker-compose.yml from passwords that are listed.
    composed = {"DATABASE_URL", "REDIS_URL"}
    missing = sorted(f.upper() for f in Settings.model_fields if f.upper() not in listed | composed)
    assert not missing, f"add to .env.example: {missing}"


def test_worker_has_a_connection_for_the_job_and_one_for_event_handlers() -> None:
    """The outbox delivery holds a connection while handlers (search indexing) open another;
    with a pool of 1 the event is never delivered (found by make smoke, Phase 3)."""
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["worker"]["environment"]
    assert int(env["DB_POOL_SIZE"]) + int(env["DB_MAX_OVERFLOW"]) >= 2
