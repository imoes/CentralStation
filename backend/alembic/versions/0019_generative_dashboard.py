"""dashboards.rationale + generated_at for AI-composed generative dashboard

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-31
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dashboards", sa.Column("rationale", sa.Text(), nullable=True))
    op.add_column("dashboards", sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("dashboards", "generated_at")
    op.drop_column("dashboards", "rationale")
