"""Jinja2 environment shared by the core and the modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from baza_wiedzy import __version__
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.web.i18n import DEFAULT_LOCALE, get_translations

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _request_context(request: Request) -> dict[str, Any]:
    """Current user, permission check and CSRF token for every template."""
    user = getattr(request.state, "user", None)

    def has_perm(code: str) -> bool:
        return user is not None and bool(user.can(code))

    return {
        "current_user": user,
        "has_perm": has_perm,
        "csrf_token": getattr(request.state, "csrf_token", ""),
    }


def make_templates(settings: Settings) -> Jinja2Templates:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(default=True, default_for_string=True),
        extensions=["jinja2.ext.i18n"],
    )
    env.install_gettext_translations(get_translations(DEFAULT_LOCALE), newstyle=True)  # type: ignore[attr-defined]
    env.globals["app_name"] = settings.app_name
    env.globals["app_version"] = __version__
    env.globals["lang"] = DEFAULT_LOCALE
    return Jinja2Templates(env=env, context_processors=[_request_context])
