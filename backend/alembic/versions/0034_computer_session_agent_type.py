"""agent_type Spalte für computer_sessions (hermes | claude_cli | codex_cli)

Revision ID: 0034
Revises: 0033
Create Date: 2026-06-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0034'
down_revision: Union[str, None] = '0033'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL füllt alle bestehenden Rows sofort mit 'hermes'.
    op.add_column(
        'computer_sessions',
        sa.Column('agent_type', sa.String(20), nullable=False, server_default='hermes'),
    )
    # Explizite Datenmigration — idempotent, migriert bestehende Sessions nach 'hermes'.
    op.execute("UPDATE computer_sessions SET agent_type = 'hermes' WHERE agent_type IS NULL OR agent_type = ''")


def downgrade() -> None:
    op.drop_column('computer_sessions', 'agent_type')
