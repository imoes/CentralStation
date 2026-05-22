"""add llm api mode setting

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO global_settings (key, value_plain, is_secret)
            SELECT 'llm.api_mode', 'chat_completions', false
            WHERE NOT EXISTS (
                SELECT 1 FROM global_settings WHERE key = 'llm.api_mode'
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM global_settings WHERE key = 'llm.api_mode'"))
