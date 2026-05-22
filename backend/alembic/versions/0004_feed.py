"""feed preferences columns

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-22
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_preferences",
        sa.Column("feed_checkmk_min_age_minutes", sa.Integer(), nullable=True, server_default="5"),
    )
    op.add_column(
        "user_preferences",
        sa.Column("feed_sources_enabled", sa.JSON(), nullable=True),
    )
    op.add_column(
        "user_preferences",
        sa.Column("feed_teams_channels", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_preferences", "feed_teams_channels")
    op.drop_column("user_preferences", "feed_sources_enabled")
    op.drop_column("user_preferences", "feed_checkmk_min_age_minutes")
