"""computer_sessions table for Computer Console session persistence

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-08
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
# Merge of both 0022 branches (0022_computer_console + 0022_ui_language)
down_revision: Union[str, tuple] = ("0022", "0022_ui_language")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "computer_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(100), nullable=False, server_default="Session"),
        sa.Column("msg_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_computer_sessions_user_id", "computer_sessions", ["user_id"])
    op.create_index("ix_computer_sessions_created_at", "computer_sessions", ["created_at"])


def downgrade() -> None:
    op.drop_table("computer_sessions")
