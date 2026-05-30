"""Add ticket_seen_map to user_preferences

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-30
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_preferences", sa.Column("ticket_seen_map", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_preferences", "ticket_seen_map")
