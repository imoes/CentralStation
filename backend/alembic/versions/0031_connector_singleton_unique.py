"""Partial unique index: max. ein Connector pro Typ pro User (außer mcp_server, awx_ng)

Revision ID: 0031
Revises: 0030
Create Date: 2026-06-23

"""
from typing import Sequence, Union

from alembic import op

revision: str = '0031'
down_revision: Union[str, None] = '0030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE UNIQUE INDEX uq_connector_owner_singleton
        ON connector_configs (owner_user_id, type)
        WHERE owner_user_id IS NOT NULL
          AND type NOT IN ('mcp_server', 'awx_ng')
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_connector_owner_singleton")
