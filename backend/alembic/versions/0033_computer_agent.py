"""computer_agent Spalte für User-Preferences (hermes | claude_cli | codex_cli)

Revision ID: 0033
Revises: 0032
Create Date: 2026-06-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0033'
down_revision: Union[str, None] = '0032'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'user_preferences',
        sa.Column('computer_agent', sa.String(20), nullable=False, server_default='hermes'),
    )


def downgrade() -> None:
    op.drop_column('user_preferences', 'computer_agent')
