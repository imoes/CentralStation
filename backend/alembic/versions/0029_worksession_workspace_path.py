"""worksession workspace_path (Werkbank Web-IDE)

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0029'
down_revision: Union[str, None] = '0028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('work_sessions', sa.Column('workspace_path', sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column('work_sessions', 'workspace_path')
