"""multiple dashboards per user

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_dashboards_user_id", "dashboards", ["user_id"])
    op.add_column("dashboard_widgets", sa.Column("dashboard_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_dashboard_widgets_dashboard_id_dashboards",
        "dashboard_widgets",
        "dashboards",
        ["dashboard_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_dashboard_widgets_dashboard_id", "dashboard_widgets", ["dashboard_id"])


def downgrade() -> None:
    op.drop_index("ix_dashboard_widgets_dashboard_id", table_name="dashboard_widgets")
    op.drop_constraint("fk_dashboard_widgets_dashboard_id_dashboards", "dashboard_widgets", type_="foreignkey")
    op.drop_column("dashboard_widgets", "dashboard_id")
    op.drop_index("ix_dashboards_user_id", table_name="dashboards")
    op.drop_table("dashboards")
