"""Alert collaboration: claim/ownership/status + comment timeline

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-06
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_collaboration",
        sa.Column("external_id", sa.String(255), primary_key=True),
        sa.Column("claimed_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("claimed_by_name", sa.String(200), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("work_status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_alert_collaboration_claimed_by", "alert_collaboration", ["claimed_by"])

    op.create_table(
        "alert_comments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="comment"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_alert_comments_external_id", "alert_comments", ["external_id"])
    op.create_index("ix_alert_comments_created_at", "alert_comments", ["created_at"])


def downgrade() -> None:
    op.drop_table("alert_comments")
    op.drop_table("alert_collaboration")
