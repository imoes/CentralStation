"""relevanz_definition.objekte: Objekte (gespeicherte Suchen) für Regeln wie auf einer Firewall

Revision ID: 0060
Revises: 0059
Create Date: 2026-11-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0060"
down_revision: Union[str, None] = "0059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("relevanz_definition", sa.Column("objekte", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("relevanz_definition", "objekte")
