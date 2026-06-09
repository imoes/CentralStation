"""add external_id + resolved to computer_sessions (GELÖST button persistence)

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-09
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, tuple] = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "computer_sessions",
        sa.Column("external_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "computer_sessions",
        sa.Column("resolved", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("computer_sessions", "resolved")
    op.drop_column("computer_sessions", "external_id")
