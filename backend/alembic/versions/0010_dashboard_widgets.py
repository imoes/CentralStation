"""dashboard_widgets table

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = inspector.get_table_names()

    if "dashboard_widgets" not in tables:
        op.create_table(
            "dashboard_widgets",
            sa.Column("id", sa.UUID(), primary_key=True),
            sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("widget_type", sa.String(20), nullable=False),
            sa.Column("title", sa.String(100), nullable=False),
            sa.Column("gs_x", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("gs_y", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("gs_w", sa.Integer(), nullable=False, server_default="4"),
            sa.Column("gs_h", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_dashboard_widgets_user_id", "dashboard_widgets", ["user_id"])
    else:
        # Table already exists from an earlier migration — ensure index exists
        existing_indexes = [idx["name"] for idx in inspector.get_indexes("dashboard_widgets")]
        if "ix_dashboard_widgets_user_id" not in existing_indexes:
            op.create_index("ix_dashboard_widgets_user_id", "dashboard_widgets", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_dashboard_widgets_user_id", table_name="dashboard_widgets")
    op.drop_table("dashboard_widgets")
