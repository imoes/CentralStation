"""per-user CheckMK OS filter preference

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-23
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_preferences", sa.Column("checkmk_os", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_preferences", "checkmk_os")
