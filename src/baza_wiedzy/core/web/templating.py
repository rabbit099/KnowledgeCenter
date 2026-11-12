"""Jinja2 environment shared by the core and the modules."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.web.i18n import DEFAULT_LOCALE, get_translations

TEMPLATE_DIR = Path(__file__).parent / "templates"


def make_templates(settings: Settings) -> Jinja2Templates:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(default=True, default_for_string=True),
        extensions=["jinja2.ext.i18n"],
    )
    env.install_gettext_translations(get_translations(DEFAULT_LOCALE), newstyle=True)  # type: ignore[attr-defined]
    env.globals["app_name"] = settings.app_name
    env.globals["app_version"] = settings.app_version
    env.globals["lang"] = DEFAULT_LOCALE
    return Jinja2Templates(env=env)
