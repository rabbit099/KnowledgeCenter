"""Module manifests and the module registry.

A module is a package `baza_wiedzy.modules.<name>` with a `module.py` that exposes
`manifest: ModuleManifest`. The registry checks dependencies at startup and collects what the
modules contribute (permissions, menu items, routers, ...). Only modules inside this repository
are loaded; there are no external plugins.

Manifest fields that point to code (routers, tasks, CLI commands, settings schema) are
"object references" of the form `package.module:attribute`, resolved lazily with `load_ref`,
so that reading manifests never imports web frameworks or heavy libraries.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any

from baza_wiedzy.core.events import EventBus

CORE_NAME = "core"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ModuleError(Exception):
    """Base class for module registry errors."""


class UnknownModuleError(ModuleError):
    pass


class ModuleDependencyError(ModuleError):
    pass


class DuplicatePermissionError(ModuleError):
    pass


@dataclass(frozen=True)
class Permission:
    code: str
    description: str


@dataclass(frozen=True)
class MenuItem:
    label: str  # gettext message id, translated in the UI
    path: str
    permission: str | None = None  # permission code needed to see the item
    order: int = 100


@dataclass(frozen=True)
class ModuleManifest:
    name: str
    version: str
    requires: tuple[str, ...] = ()
    permissions: tuple[Permission, ...] = ()
    menu: tuple[MenuItem, ...] = ()
    routers: tuple[str, ...] = ()  # object refs to FastAPI routers
    templates: str | None = None  # package holding Jinja2 templates
    migrations_branch: str | None = None  # Alembic branch label (one branch per module)
    migrations_path: str | None = None  # package holding the module's Alembic versions
    settings_schema: str | None = None  # object ref to a pydantic model of admin settings
    tasks: tuple[str, ...] = ()  # object refs to background tasks (Dramatiq actors)
    cli_commands: tuple[str, ...] = ()  # object refs to CLI command callables
    on_register: Callable[[EventBus], None] | None = None  # subscribe to events here

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ModuleError(f"Invalid module name: {self.name!r}")


def load_ref(ref: str) -> Any:
    """Resolve an object reference `package.module:attribute`."""
    module_name, _, attribute = ref.partition(":")
    if not module_name or not attribute:
        raise ModuleError(f"Invalid object reference: {ref!r} (expected 'package.module:name')")
    return getattr(importlib.import_module(module_name), attribute)


def discover(name: str) -> ModuleManifest:
    """Load the manifest of the repository module `name`."""
    if name == CORE_NAME:
        module_path = "baza_wiedzy.core.module"
    elif _NAME_RE.match(name):
        module_path = f"baza_wiedzy.modules.{name}.module"
    else:
        raise UnknownModuleError(f"Invalid module name in ENABLED_MODULES: {name!r}")
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        if exc.name and module_path.startswith(exc.name):
            raise UnknownModuleError(
                f"Unknown module {name!r} in ENABLED_MODULES (no {module_path})."
            ) from exc
        raise
    manifest = getattr(module, "manifest", None)
    if not isinstance(manifest, ModuleManifest) or manifest.name != name:
        raise ModuleError(
            f"{module_path} must define manifest = ModuleManifest(name={name!r}, ...)"
        )
    return manifest


class ModuleRegistry:
    """The set of enabled modules, validated and ordered so dependencies come first."""

    def __init__(self, modules: tuple[ModuleManifest, ...]) -> None:
        self.modules = modules
        self._by_name = {m.name: m for m in modules}

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def get(self, name: str) -> ModuleManifest:
        return self._by_name[name]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(m.name for m in self.modules)

    def permissions(self) -> tuple[Permission, ...]:
        """The permission catalog: all codes contributed by enabled modules, sorted by code."""
        owners: dict[str, str] = {}
        catalog: list[Permission] = []
        for manifest in self.modules:
            for permission in manifest.permissions:
                if permission.code in owners:
                    raise DuplicatePermissionError(
                        f"Permission {permission.code!r} is declared by both "
                        f"{owners[permission.code]!r} and {manifest.name!r}."
                    )
                owners[permission.code] = manifest.name
                catalog.append(permission)
        return tuple(sorted(catalog, key=lambda p: p.code))

    def menu(self) -> tuple[MenuItem, ...]:
        items = [item for m in self.modules for item in m.menu]
        return tuple(sorted(items, key=lambda i: (i.order, i.label)))

    def register_events(self, bus: EventBus) -> None:
        for manifest in self.modules:
            if manifest.on_register is not None:
                manifest.on_register(bus)


def _with_core(enabled: Iterable[str]) -> list[str]:
    return [CORE_NAME, *(n for n in dict.fromkeys(enabled) if n != CORE_NAME)]


def build_registry(
    enabled: Iterable[str], available: Mapping[str, ModuleManifest]
) -> ModuleRegistry:
    """Validate the enabled modules against `available` manifests and order them.

    The core is always included. Raises `UnknownModuleError` for names without a manifest and
    `ModuleDependencyError` (with a readable message) for missing requirements or cycles.
    """
    names = _with_core(enabled)
    unknown = [n for n in names if n not in available]
    if unknown:
        raise UnknownModuleError(f"Unknown module(s): {', '.join(unknown)}.")

    problems = [
        f"module {name!r} requires {dep!r}, which is not enabled (add it to ENABLED_MODULES)"
        for name in names
        for dep in available[name].requires
        if dep not in names
    ]
    if problems:
        raise ModuleDependencyError("; ".join(problems) + ".")

    sorter = TopologicalSorter({n: sorted(available[n].requires) for n in names})
    try:
        ordered = list(sorter.static_order())
    except CycleError as exc:
        cycle = " -> ".join(str(n) for n in exc.args[1])
        raise ModuleDependencyError(f"Circular module dependency: {cycle}.") from exc
    # The core has no dependencies and must come first for a predictable startup order.
    ordered.sort(key=lambda n: n != CORE_NAME)
    return ModuleRegistry(tuple(available[n] for n in ordered))


def load_registry(enabled: Sequence[str]) -> ModuleRegistry:
    """Discover manifests of the named modules (plus the core) and build the registry."""
    names = _with_core(enabled)
    return build_registry(names, {n: discover(n) for n in names})
