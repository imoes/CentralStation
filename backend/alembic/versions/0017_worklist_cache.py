"""ai_insight_cache + worklist_snapshots for AI-prioritised bridge worklist

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-30
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_insight_cache",
        sa.Column("cache_key", sa.String(120), primary_key=True),
        sa.Column("severity", sa.String(12), nullable=True),
        sa.Column("sample_title", sa.String(300), nullable=True),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "worklist_snapshots",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
        sa.Column("items", sa.JSON(), server_default="[]"),
        sa.Column("alert_state", sa.String(10), server_default="green"),
        sa.Column("open_count", sa.Integer(), server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("worklist_snapshots")
    op.drop_table("ai_insight_cache")
