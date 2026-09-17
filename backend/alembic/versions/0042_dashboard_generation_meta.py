"""add provenance metadata to generative dashboards

Revision ID: 0042
Revises: 0041
Create Date: 2026-09-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dashboards", sa.Column("generation_meta", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("dashboards", "generation_meta")
