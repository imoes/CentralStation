"""netz_zustand, netz_muster: Switch-Durchlauf der Log-Überwachung (eigener Drain-Lerner)

Revision ID: 0059
Revises: 0058
Create Date: 2026-11-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0059"
down_revision: Union[str, None] = "0058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "netz_zustand",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("gestartet_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("miner_state", sa.LargeBinary, nullable=True),
        sa.Column("letztes_fenster", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "netz_muster",
        sa.Column("key", sa.String(16), primary_key=True),
        sa.Column("template", sa.Text, nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("beispiel", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("netz_muster")
    op.drop_table("netz_zustand")
