"""relevanz_definition: versionierte Definition, was relevant ist und was nicht

Revision ID: 0056
Revises: 0055
Create Date: 2026-11-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0056"
down_revision: Union[str, None] = "0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "relevanz_definition",
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("kriterien", postgresql.JSONB, nullable=False),
        sa.Column("parameter", postgresql.JSONB, nullable=False),
        sa.Column("begruendung", sa.String(1000), nullable=False),
        sa.Column("erstellt_von", sa.String(255), nullable=False),
        sa.Column("erstellt_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("relevanz_definition")
