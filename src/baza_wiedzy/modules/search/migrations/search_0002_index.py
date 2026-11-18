"""search: the index tables srch_parts and srch_documents (Phase 3, ADR-026).

Revision ID: search_0002
Revises: search_0001
Create Date: 2026-11-18

Copies of the extracted text (owned by ingestion, ADR-020) and of the metadata (owned by
documents) with generated tsvectors in the configurations pl, english and simple_unaccent, GIN
indexes, and a trigram index for near matches of titles, file names and e-mail headers. The
tables start empty: the worker indexes every extracted version at its next start (search
startup hook), or run `baza-wiedzy reindex-search --missing`. Expand-only; older application
versions ignore the tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "search_0002"
down_revision: str | Sequence[str] | None = "search_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = ("ingestion_0001", "documents_0004")

UUID = pg.UUID(as_uuid=True)
CONFIGS = {"pl": "public.pl", "en": "pg_catalog.english", "simple": "public.simple_unaccent"}


def _content(config: str) -> str:
    return (
        f"to_tsvector('{config}'::regconfig, text_content) || "
        f"to_tsvector('{config}'::regconfig, ident_terms)"
    )


def _meta(config: str) -> str:
    c = f"'{config}'::regconfig"
    return (
        f"setweight(to_tsvector({c}, meta_a), 'A') || setweight(to_tsvector({c}, meta_b), 'B')"
        f" || setweight(to_tsvector({c}, ident_terms), 'B')"
    )


def _fks(table: str) -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["doc_versions.id"],
            ondelete="CASCADE",
            name=f"fk_{table}_version_id_doc_versions",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["doc_documents.id"],
            ondelete="CASCADE",
            name=f"fk_{table}_document_id_doc_documents",
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "srch_parts",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("version_id", UUID, nullable=False),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("part_type", sa.Text, nullable=False),
        sa.Column("origin", pg.JSONB, nullable=False),
        sa.Column("page_no", sa.Integer),
        sa.Column("chunk_no", sa.Integer, nullable=False),
        sa.Column("language", sa.Text, nullable=False),
        sa.Column("text_content", sa.Text, nullable=False),
        sa.Column("ident_terms", sa.Text, nullable=False, server_default=""),
        *(
            sa.Column(f"tsv_{key}", pg.TSVECTOR, sa.Computed(_content(cfg), persisted=True))
            for key, cfg in CONFIGS.items()
        ),
        *_fks("srch_parts"),
        sa.PrimaryKeyConstraint("id", name="pk_srch_parts"),
        sa.UniqueConstraint("version_id", "seq", name="uq_srch_parts_version_id_seq"),
    )
    op.create_index("ix_srch_parts_document_id", "srch_parts", ["document_id"])
    for key in CONFIGS:
        op.create_index(f"ix_srch_parts_tsv_{key}", "srch_parts", [f"tsv_{key}"],
                        postgresql_using="gin")  # fmt: skip

    op.create_table(
        "srch_documents",
        sa.Column("version_id", UUID, nullable=False),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("meta_a", sa.Text, nullable=False),
        sa.Column("meta_b", sa.Text, nullable=False),
        sa.Column("ident_terms", sa.Text, nullable=False, server_default=""),
        sa.Column("meta_fold", sa.Text, nullable=False),
        *(
            sa.Column(f"tsv_meta_{key}", pg.TSVECTOR, sa.Computed(_meta(cfg), persisted=True))
            for key, cfg in CONFIGS.items()
        ),
        sa.Column(
            "indexed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        *_fks("srch_documents"),
        sa.PrimaryKeyConstraint("version_id", name="pk_srch_documents"),
    )
    op.create_index("ix_srch_documents_document_id", "srch_documents", ["document_id"])
    for key in CONFIGS:
        op.create_index(f"ix_srch_documents_tsv_{key}", "srch_documents", [f"tsv_meta_{key}"],
                        postgresql_using="gin")  # fmt: skip
    op.create_index(
        "ix_srch_documents_meta_fold_trgm",
        "srch_documents",
        ["meta_fold"],
        postgresql_using="gin",
        postgresql_ops={"meta_fold": "gin_trgm_ops"},
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; restore a backup to go back.")
