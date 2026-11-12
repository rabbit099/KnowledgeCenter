"""Search baseline: Polish Hunspell dictionary and text-search configurations.

Revision ID: search_0001
Revises:
Create Date: 2026-11-12

Needs the dictionary files of the `db` image (ADR-016). Configurations (docs/en/search.md):
- `pl`: Hunspell (`pl_hunspell`, stop words `polish`), unknown words fall through to `simple`;
- `simple_unaccent`: lowercase without diacritics, for queries typed without Polish letters.
The built-in `english` configuration is used as is. Changing any of these later needs a new
migration and `baza-wiedzy reindex-search`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "search_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("search",)
depends_on: str | Sequence[str] | None = ("core_0001",)

WORD_TOKENS = (
    "asciiword, word, numword, asciihword, hword, numhword, hword_asciipart, hword_part, "
    "hword_numpart"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TEXT SEARCH DICTIONARY public.pl_hunspell (
            TEMPLATE = ispell, DictFile = pl_pl, AffFile = pl_pl, StopWords = polish
        )
        """
    )
    op.execute("CREATE TEXT SEARCH CONFIGURATION public.pl (COPY = pg_catalog.simple)")
    op.execute(
        f"ALTER TEXT SEARCH CONFIGURATION public.pl ALTER MAPPING FOR {WORD_TOKENS} "
        "WITH public.pl_hunspell, pg_catalog.simple"
    )
    op.execute("CREATE TEXT SEARCH CONFIGURATION public.simple_unaccent (COPY = pg_catalog.simple)")
    op.execute(
        f"ALTER TEXT SEARCH CONFIGURATION public.simple_unaccent ALTER MAPPING FOR {WORD_TOKENS} "
        "WITH public.unaccent, pg_catalog.simple"
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; restore a backup to go back.")
