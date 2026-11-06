"""relevanz_definition.regeln: Regelwerk mit Firewall-Semantik (forward/block)

Revision ID: 0058
Revises: 0057
Create Date: 2026-11-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0058"
down_revision: Union[str, None] = "0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("relevanz_definition", sa.Column("regeln", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("relevanz_definition", "regeln")
