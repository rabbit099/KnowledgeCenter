"""GitHub configuration parses and has the expected structure (independent of actionlint).

A YAML syntax error in a workflow makes GitHub refuse the whole file ("Invalid workflow
file"), so no job runs at all; this test fails locally first. actionlint (make lint) checks
the workflow semantics on top.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
SHA_PIN = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def load(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - the failure message is the point
        pytest.fail(f"{path.relative_to(ROOT)}: invalid YAML: {exc}")


def test_there_are_workflows() -> None:
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "release.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_structure(path: Path) -> None:
    workflow = load(path)
    assert isinstance(workflow, dict)
    # YAML 1.1 reads the key `on` as the boolean True.
    assert "name" in workflow and ("on" in workflow or True in workflow)
    assert isinstance(workflow.get("jobs"), dict) and workflow["jobs"]
    for job_id, job in workflow["jobs"].items():
        assert "runs-on" in job or "uses" in job, f"{path.name}: job {job_id}"
        for step in job.get("steps", []):
            assert ("uses" in step) != ("run" in step), f"{path.name}: {job_id}: {step}"
            for key in ("name", "run", "if"):
                if key in step:
                    assert isinstance(step[key], str | bool), f"{path.name}: {job_id}: {key}"
            uses = step.get("uses")
            if uses and not uses.startswith("./"):
                assert SHA_PIN.match(uses), f"{path.name}: {uses} is not pinned to a commit SHA"


def test_dependabot_structure() -> None:
    config = load(ROOT / ".github" / "dependabot.yml")
    assert config["version"] == 2
    assert config["updates"]
    for update in config["updates"]:
        assert update["package-ecosystem"] and update["schedule"]["interval"]
        assert update.get("directory") or update.get("directories")


def test_a_colon_in_an_unquoted_name_is_caught() -> None:
    """The bug of 2026-11-17 (ci.yml line 38): `name: text (a: b)` is not valid YAML."""
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load("steps:\n  - name: Install (with the worker extra: x)\n    run: true\n")
