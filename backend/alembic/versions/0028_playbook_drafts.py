"""playbook_drafts table

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0028'
down_revision: Union[str, None] = '0027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'playbook_drafts',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column('title', sa.String(512), nullable=False),
        sa.Column('yaml', sa.Text(), nullable=False),
        sa.Column('target', sa.String(255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(30), nullable=False, server_default='drafted', index=True),
        sa.Column('awx_template_id', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('playbook_drafts')
