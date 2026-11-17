"""Jinja2 environment shared by the core and the modules."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import BaseLoader, ChoiceLoader, Environment, FileSystemLoader, select_autoescape

from baza_wiedzy import __version__
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.web.i18n import DEFAULT_LOCALE, get_translations

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _request_context(request: Request) -> dict[str, Any]:
    """Current user, permission check and CSRF token for every template."""
    user = getattr(request.state, "user", None)

    def has_perm(code: str) -> bool:
        return user is not None and bool(user.can(code))

    registry = getattr(request.app.state, "registry", None)
    main_nav = [
        item
        for item in (registry.menu("main") if registry is not None and user is not None else ())
        if item.permission is None or has_perm(item.permission)
    ]
    return {
        "current_user": user,
        "has_perm": has_perm,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "main_nav": main_nav,
    }


def require_csrf(token: object) -> str:
    """The CSRF token for a hidden form field; an empty or undefined token is a template bug."""
    if not isinstance(token, str) or not token:
        raise RuntimeError(
            "CSRF token missing in a form: import the csrf macro 'with context' "
            "(admin/_macros.html)"
        )
    return token


def filesize(size: int | None) -> str:
    """Human-readable size with a Polish decimal comma: 17 B, 1,5 KB, 12,3 MB."""
    if size is None:
        return ""
    if size < 1024:
        return f"{size} B"
    for unit, factor in (("KB", 1024), ("MB", 1024**2), ("GB", 1024**3)):
        if size < factor * 1024 or unit == "GB":
            return f"{size / factor:.1f} {unit}".replace(".", ",")
    return f"{size} B"  # pragma: no cover


HASH_RE = re.compile(r"^[0-9a-f]{32,}$")


def is_hash(value: object) -> bool:
    """Long hexadecimal values (SHA-256 and similar) are shortened in tables."""
    return isinstance(value, str) and bool(HASH_RE.match(value))


def make_templates(settings: Settings, module_packages: tuple[str, ...] = ()) -> Jinja2Templates:
    """Core templates first, then each module's `templates/` folder (manifest `templates`)."""
    loaders: list[BaseLoader] = [FileSystemLoader(TEMPLATE_DIR)]
    for package in module_packages:
        spec = importlib.util.find_spec(package)
        if spec and spec.submodule_search_locations:
            folder = Path(next(iter(spec.submodule_search_locations))) / "templates"
            loaders.append(FileSystemLoader(folder))
    env = Environment(
        loader=ChoiceLoader(loaders),
        autoescape=select_autoescape(default=True, default_for_string=True),
        extensions=["jinja2.ext.i18n"],
    )
    env.install_gettext_translations(get_translations(DEFAULT_LOCALE), newstyle=True)  # type: ignore[attr-defined]
    env.globals["app_name"] = settings.app_name
    env.globals["app_version"] = __version__
    # Templates of one module may offer links to pages of another module only when it is on.
    env.globals["enabled_modules"] = frozenset(settings.enabled_modules)
    env.globals["lang"] = DEFAULT_LOCALE
    env.globals["require_csrf"] = require_csrf
    env.globals["is_hash"] = is_hash
    env.filters["filesize"] = filesize
    # The database stores UTC; the UI shows Europe/Warsaw (CLAUDE.md section 6).
    env.globals["tz"] = ZoneInfo("Europe/Warsaw")
    return Jinja2Templates(env=env, context_processors=[_request_context])
