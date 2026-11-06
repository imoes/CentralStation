"""field_catalog: alle Felder der Log-Quellen mit Rolle, Kardinalität und Abdeckung

Revision ID: 0054
Revises: 0053
Create Date: 2026-11-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054"
down_revision: Union[str, None] = "0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "field_catalog",
        sa.Column("quelle", sa.String(32), primary_key=True),
        sa.Column("feld", sa.String(255), primary_key=True),
        sa.Column("typ", sa.String(64), nullable=False),
        sa.Column("numerisch", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("rolle", sa.String(16), nullable=False),
        sa.Column("rolle_herkunft", sa.String(16), nullable=False, server_default="regel"),
        sa.Column("rolle_grund", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="aktiv"),
        sa.Column("kardinalitaet", sa.Integer, nullable=True),
        sa.Column("mit_feld", sa.Integer, nullable=True),
        sa.Column("abdeckung", sa.Float, nullable=True),
        sa.Column("beispielwerte", postgresql.JSONB, nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gemessen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_field_catalog_rolle", "field_catalog", ["quelle", "rolle"])


def downgrade() -> None:
    op.drop_index("ix_field_catalog_rolle", table_name="field_catalog")
    op.drop_table("field_catalog")
