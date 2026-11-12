"""Translations (gettext catalogs managed with Babel).

The `.po` files are the source in the repository. They are compiled in memory at startup,
so there is no build step and no stale `.mo` file. Message ids are English; the UI is Polish.
"""

from __future__ import annotations

from functools import cache
from io import BytesIO
from pathlib import Path

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from babel.support import NullTranslations, Translations

LOCALE_DIR = Path(__file__).parent / "locale"
DEFAULT_LOCALE = "pl"
DOMAIN = "messages"


@cache
def get_translations(locale: str = DEFAULT_LOCALE) -> NullTranslations:
    po_path = LOCALE_DIR / locale / "LC_MESSAGES" / f"{DOMAIN}.po"
    if not po_path.is_file():
        return NullTranslations()
    with po_path.open("rb") as po_file:
        catalog = read_po(po_file, locale=locale)
    buffer = BytesIO()
    write_mo(buffer, catalog)
    buffer.seek(0)
    return Translations(buffer, domain=DOMAIN)
