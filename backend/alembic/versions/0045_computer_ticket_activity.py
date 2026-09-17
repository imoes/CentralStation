"""persist the Jira activity baseline for Computer Console sessions

Revision ID: 0045
Revises: 0044
Create Date: 2026-09-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "computer_sessions",
        sa.Column("ticket_activity_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "computer_sessions",
        sa.Column("context_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE computer_sessions
           SET context_synced_at = created_at
         WHERE ticket_connector_id IS NOT NULL
           AND ticket_issue_id IS NOT NULL
           AND context_hash IS NOT NULL
           AND context_synced_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("computer_sessions", "context_synced_at")
    op.drop_column("computer_sessions", "ticket_activity_snapshot")
