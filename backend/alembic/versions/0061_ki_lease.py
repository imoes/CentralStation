"""ki_lease: Agentenflotte — ein Host gehört während einer Analyse genau einem Agenten

Revision ID: 0061
Revises: 0060
Create Date: 2026-11-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0061"
down_revision: Union[str, None] = "0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ki_lease",
        sa.Column("host", sa.String(255), primary_key=True),
        sa.Column("agent", sa.String(64), nullable=False, index=True),
        sa.Column("fall", sa.String(255), nullable=False),
        sa.Column("seit", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bis", sa.DateTime(timezone=True), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("ki_lease")
