"""raster_lauf: Trockenlauf des Log-Rasters je 5-min-Fenster und Quelle

Revision ID: 0055
Revises: 0054
Create Date: 2026-11-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0055"
down_revision: Union[str, None] = "0054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "raster_lauf",
        sa.Column("fenster_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("quelle", sa.String(16), primary_key=True),
        sa.Column("werte", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("raster_lauf")
