"""ai_analyses.clusters (KI-Insights Fehler-Cluster)

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0030'
down_revision: Union[str, None] = '0029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ai_analyses', sa.Column('clusters', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('ai_analyses', 'clusters')
