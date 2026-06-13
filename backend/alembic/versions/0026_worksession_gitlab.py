"""worksession_gitlab_columns

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0026'
down_revision: Union[str, None] = '0025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('work_sessions', sa.Column('gitlab_project_id', sa.String(64), nullable=True))
    op.add_column('work_sessions', sa.Column('gitlab_branch', sa.String(255), nullable=True))
    op.add_column('work_sessions', sa.Column('gitlab_mr_iid', sa.Integer(), nullable=True))
    op.add_column('work_sessions', sa.Column('gitlab_mr_url', sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column('work_sessions', 'gitlab_mr_url')
    op.drop_column('work_sessions', 'gitlab_mr_iid')
    op.drop_column('work_sessions', 'gitlab_branch')
    op.drop_column('work_sessions', 'gitlab_project_id')
