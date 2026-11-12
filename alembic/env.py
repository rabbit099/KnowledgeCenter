"""Alembic environment: one branch per module plus `core` (CLAUDE.md section 6a)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool, text

from baza_wiedzy.core.config import Settings

# Any constant works; it only has to be the same for every `migrate` run.
MIGRATION_LOCK_ID = 7_172_026

config = context.config


def run_migrations_online() -> None:
    url = config.attributes.get("database_url") or Settings().database_url
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        # Two `migrate` runs at the same time wait for each other instead of colliding.
        connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATION_LOCK_ID})
        connection.commit()
        try:
            context.configure(
                connection=connection,
                target_metadata=None,
                transaction_per_migration=True,
            )
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATION_LOCK_ID})
            connection.commit()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (--sql) migrations are not supported; run `baza-wiedzy migrate`.")
run_migrations_online()
