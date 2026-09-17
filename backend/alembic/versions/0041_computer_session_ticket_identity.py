"""persist stable Jira identity on computer sessions

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("computer_sessions", sa.Column("ticket_connector_id", sa.Uuid(), nullable=True))
    op.add_column("computer_sessions", sa.Column("ticket_issue_id", sa.String(255), nullable=True))
    op.add_column("computer_sessions", sa.Column("ticket_key", sa.String(100), nullable=True))
    op.add_column("computer_sessions", sa.Column("context_hash", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_computer_sessions_ticket_connector",
        "computer_sessions", "connector_configs",
        ["ticket_connector_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index(
        "ix_computer_sessions_ticket_connector_id",
        "computer_sessions", ["ticket_connector_id"], unique=False,
    )
    op.create_unique_constraint(
        "uq_computer_session_ticket",
        "computer_sessions", ["user_id", "ticket_connector_id", "ticket_issue_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_computer_session_ticket", "computer_sessions", type_="unique")
    op.drop_index("ix_computer_sessions_ticket_connector_id", table_name="computer_sessions")
    op.drop_constraint("fk_computer_sessions_ticket_connector", "computer_sessions", type_="foreignkey")
    op.drop_column("computer_sessions", "context_hash")
    op.drop_column("computer_sessions", "ticket_key")
    op.drop_column("computer_sessions", "ticket_issue_id")
    op.drop_column("computer_sessions", "ticket_connector_id")
