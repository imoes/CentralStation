"""track the last activity of Computer Console sessions

Revision ID: 0046
Revises: 0045
Create Date: 2026-09-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: Union[str, None] = "0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "computer_sessions",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
    )
    # PostgreSQL fills existing rows with the server default while adding the
    # column. Replace that migration timestamp with each session's real baseline.
    op.execute("UPDATE computer_sessions SET last_activity_at = created_at")
    op.alter_column("computer_sessions", "last_activity_at", nullable=False)
    op.create_index(
        "ix_computer_sessions_last_activity_at",
        "computer_sessions",
        ["last_activity_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_computer_sessions_last_activity_at", table_name="computer_sessions")
    op.drop_column("computer_sessions", "last_activity_at")
