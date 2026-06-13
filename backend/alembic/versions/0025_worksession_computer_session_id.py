"""worksession_computer_session_id

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0025'
down_revision: Union[str, None] = '0024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('work_sessions', sa.Column('computer_session_id', sa.String(64), nullable=True))
    op.create_index('ix_work_sessions_computer_session_id', 'work_sessions', ['computer_session_id'])


def downgrade() -> None:
    op.drop_index('ix_work_sessions_computer_session_id', table_name='work_sessions')
    op.drop_column('work_sessions', 'computer_session_id')
