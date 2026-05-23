"""per-user checkmk agent filter preferences (locations, ve, criticality)

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-23
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_preferences", sa.Column("checkmk_locations",   sa.JSON(), nullable=True))
    op.add_column("user_preferences", sa.Column("checkmk_ve",          sa.JSON(), nullable=True))
    op.add_column("user_preferences", sa.Column("checkmk_criticality", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_preferences", "checkmk_criticality")
    op.drop_column("user_preferences", "checkmk_ve")
    op.drop_column("user_preferences", "checkmk_locations")
