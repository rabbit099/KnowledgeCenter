"""Alembic integration: one migration branch per module plus `core` (CLAUDE.md section 6a).

Version locations are collected from the manifests of ALL modules in the repository, so that
revisions of a module that was enabled earlier and is disabled now are still known. Only the
branches of ENABLED modules are upgraded.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import pkgutil
from collections.abc import Iterable
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.health import CheckResult, ReadyCheck
from baza_wiedzy.core.registry import CORE_NAME, ModuleManifest, ModuleRegistry, discover

log = logging.getLogger(__name__)
MODULES_PACKAGE = "baza_wiedzy.modules"


class MigrationError(Exception):
    pass


def all_manifests() -> list[ModuleManifest]:
    """The core and every module package in the repository (enabled or not)."""
    spec = importlib.util.find_spec(MODULES_PACKAGE)
    locations = spec.submodule_search_locations if spec else None
    names = sorted(m.name for m in pkgutil.iter_modules(locations or []) if m.ispkg)
    return [discover(CORE_NAME), *(discover(name) for name in names)]


def package_dir(package: str) -> Path:
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        raise MigrationError(f"Migrations package {package!r} not found.")
    return Path(next(iter(spec.submodule_search_locations)))


def _with_migrations(manifests: Iterable[ModuleManifest]) -> list[ModuleManifest]:
    return [m for m in manifests if m.migrations_branch and m.migrations_path]


def make_config(settings: Settings) -> Config:
    ini = Path(settings.alembic_ini)
    if not ini.is_file():
        raise MigrationError(
            f"Alembic configuration {ini} not found (working directory: {Path.cwd()})."
        )
    cfg = Config(str(ini))
    locations = [
        str(package_dir(m.migrations_path or "")) for m in _with_migrations(all_manifests())
    ]
    cfg.set_main_option("path_separator", "os")
    cfg.set_main_option("version_locations", os.pathsep.join(locations))
    cfg.attributes["database_url"] = settings.database_url
    return cfg


def upgrade(settings: Settings, registry: ModuleRegistry) -> list[str]:
    """Upgrade the branches of the enabled modules, dependencies first."""
    cfg = make_config(settings)
    targets = [f"{m.migrations_branch}@head" for m in _with_migrations(registry.modules)]
    for target in targets:
        command.upgrade(cfg, target)
    return targets


def expected_heads(cfg: Config, registry: ModuleRegistry) -> set[str]:
    script = ScriptDirectory.from_config(cfg)
    heads: set[str] = set()
    for manifest in _with_migrations(registry.modules):
        revision = script.get_revision(f"{manifest.migrations_branch}@head")
        if revision is not None:
            heads.add(revision.revision)
    return heads


def _as_tuple(value: str | Iterable[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def applied_revisions(script: ScriptDirectory, current: Iterable[str]) -> set[str]:
    """All revisions implied by the current heads, following down revisions and depends_on."""
    seen: set[str] = set()
    stack = list(current)
    while stack:
        rev_id = stack.pop()
        if rev_id in seen:
            continue
        seen.add(rev_id)
        revision = script.get_revision(rev_id)
        if revision is not None:
            stack.extend(_as_tuple(revision.down_revision))
            stack.extend(_as_tuple(revision.dependencies))
    return seen


def schema_status(
    expected: set[str], current: set[str], applied: set[str], unknown: set[str]
) -> CheckResult:
    """Pure comparison used by /ready (unit-tested without a database)."""
    if not current:
        return CheckResult(False, "not migrated")
    if unknown:
        # The database is newer than this application. Expand/contract guarantees that
        # version N-1 of the application still runs on the schema of version N.
        return CheckResult(True, "ok (schema newer than application)")
    if not expected <= applied:
        return CheckResult(False, "outdated")
    return CheckResult(True)


def _is_known(script: ScriptDirectory, rev_id: str) -> bool:
    try:
        return script.get_revision(rev_id) is not None
    except Exception:
        return False


def schema_check(engine: Engine, settings: Settings, registry: ModuleRegistry) -> ReadyCheck:
    def check() -> CheckResult:
        try:
            cfg = make_config(settings)
            script = ScriptDirectory.from_config(cfg)
            expected = expected_heads(cfg, registry)
            with engine.connect() as conn:
                current = set(MigrationContext.configure(conn).get_current_heads())
            unknown = {r for r in current if not _is_known(script, r)}
            applied = applied_revisions(script, current - unknown)
        except Exception:
            log.exception("readiness: schema check failed")
            return CheckResult(False, "unknown")
        return schema_status(expected, current, applied, unknown)

    return check


def new_revision(settings: Settings, branch: str, message: str) -> str:
    """Create an empty revision file in the branch of a module (no database needed)."""
    with_migrations = _with_migrations(all_manifests())
    manifests = {m.migrations_branch: m for m in with_migrations}
    manifests_by_name = {m.name: m for m in with_migrations}
    manifest = manifests.get(branch)
    if manifest is None:
        known = ", ".join(sorted(b for b in manifests if b)) or "none"
        raise MigrationError(
            f"No module has migrations_branch={branch!r} with migrations_path set "
            f"(known branches: {known})."
        )
    cfg = make_config(settings)
    script = ScriptDirectory.from_config(cfg)
    branch_revisions = [r for r in script.walk_revisions() if branch in r.branch_labels]
    rev_id = f"{branch}_{len(branch_revisions) + 1:04d}"
    if branch_revisions:
        result = command.revision(cfg, message=message, head=f"{branch}@head", rev_id=rev_id)
    else:
        # First revision of a module: new branch that depends on the core and on the
        # branches of the modules it requires (CLAUDE.md section 6a).
        depends_on = [
            head.revision
            for dep in (CORE_NAME, *manifest.requires)
            if dep != manifest.name
            and dep in manifests_by_name
            and manifests_by_name[dep].migrations_branch
            for head in [script.get_revision(f"{manifests_by_name[dep].migrations_branch}@head")]
            if head is not None
        ]
        result = command.revision(
            cfg,
            message=message,
            head="base",
            branch_label=branch,
            version_path=str(package_dir(manifest.migrations_path or "")),
            # Alembic accepts a list here (util.to_list) although it is annotated as str.
            depends_on=depends_on or None,  # type: ignore[arg-type]
            rev_id=rev_id,
        )
    scripts = result if isinstance(result, list) else [result]
    return ", ".join(s.path for s in scripts if s is not None)
