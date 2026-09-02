"""Perplexity provider settings

Seeds the three settings the Perplexity provider reads. The API key MUST exist with
is_secret=true BEFORE anyone saves a value: set_setting() creates missing rows with
is_secret=false and would then store the key in value_plain — a credential in the
clear. Seeding the row here makes the generic settings PATCH encrypt it.

Revision ID: 0040
Revises: 0039
Create Date: 2026-09-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# key, default, is_secret
_SETTINGS = [
    ("llm.perplexity_api_key", None, True),
    ("llm.perplexity_model", "anthropic/claude-sonnet-5", False),
    ("llm.perplexity_timeout_seconds", "120", False),
]


def upgrade() -> None:
    for key, default, is_secret in _SETTINGS:
        op.execute(
            sa.text(
                """
                INSERT INTO global_settings (key, value_plain, is_secret)
                SELECT :key, :val, :sec
                WHERE NOT EXISTS (
                    SELECT 1 FROM global_settings WHERE key = :key
                )
                """
            ).bindparams(key=key, val=default, sec=is_secret)
        )


def downgrade() -> None:
    for key, _default, _sec in _SETTINGS:
        op.execute(
            sa.text("DELETE FROM global_settings WHERE key = :key").bindparams(key=key)
        )
