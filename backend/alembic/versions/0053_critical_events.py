"""critical_events: schwere Kernelmeldungen je Host, Art und 5-min-Fenster

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0053"
down_revision: Union[str, None] = "0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "critical_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # key = error_clusters/critical.critical_key(signatur, host) — auch LogTemplate.key
        sa.Column("key", sa.String(16), nullable=False, index=True),
        sa.Column("signature", sa.String(32), nullable=False),
        sa.Column("host", sa.String(255), nullable=False, index=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("lines", sa.Integer, nullable=False, server_default="1"),
        sa.Column("example", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key", "window_start", name="uq_critical_events_key_window"),
    )


def downgrade() -> None:
    op.drop_table("critical_events")
