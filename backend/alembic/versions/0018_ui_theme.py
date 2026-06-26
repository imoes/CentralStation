"""user_preferences.ui_theme for app-wide theme selection

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-30
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_preferences", sa.Column("ui_theme", sa.String(20), nullable=True, server_default="classic"))


def downgrade() -> None:
    op.drop_column("user_preferences", "ui_theme")
