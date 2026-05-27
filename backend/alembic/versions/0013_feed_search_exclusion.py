"""Add is_exclusion to feed_searches

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "feed_searches",
        sa.Column("is_exclusion", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("feed_searches", "is_exclusion")
