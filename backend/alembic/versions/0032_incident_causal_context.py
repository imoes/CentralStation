"""causal_context JSON-Spalte für Incidents (kausale Incident-Korrelation)

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0032'
down_revision: Union[str, None] = '0031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'incidents',
        sa.Column('causal_context', postgresql.JSONB, nullable=True, comment=(
            'Kausale Ursachen-Liste: [{service, incident_id, likely_cause, started_at}]'
        )),
    )


def downgrade() -> None:
    op.drop_column('incidents', 'causal_context')
