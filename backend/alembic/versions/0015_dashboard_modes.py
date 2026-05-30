"""Dashboard mode + widget pinned/hidden for Generative UI

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-30
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dashboards", sa.Column("mode", sa.String(20), nullable=False, server_default="classic"))
    op.add_column("dashboard_widgets", sa.Column("pinned", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("dashboard_widgets", sa.Column("hidden", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("dashboards", "mode")
    op.drop_column("dashboard_widgets", "pinned")
    op.drop_column("dashboard_widgets", "hidden")
