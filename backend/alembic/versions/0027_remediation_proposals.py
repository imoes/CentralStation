"""remediation_proposals table

Revision ID: 0027
Revises: 0026
Create Date: 2026-06-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0027'
down_revision: Union[str, None] = '0026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'remediation_proposals',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column('external_id', sa.String(255), nullable=True, index=True),
        sa.Column('host', sa.String(255), nullable=True),
        sa.Column('finding_title', sa.String(512), nullable=False),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('awx_template_id', sa.Integer(), nullable=True),
        sa.Column('awx_template_name', sa.String(255), nullable=True),
        sa.Column('extra_vars', sa.JSON(), nullable=True),
        sa.Column('risk', sa.String(20), nullable=False, server_default='medium'),
        sa.Column('status', sa.String(30), nullable=False, server_default='proposed', index=True),
        sa.Column('awx_job_id', sa.Integer(), nullable=True),
        sa.Column('stdout', sa.Text(), nullable=True),
        sa.Column('approved_by', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('analysis_id', sa.Uuid(), sa.ForeignKey('ai_analyses.id', ondelete='SET NULL'), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('remediation_proposals')
