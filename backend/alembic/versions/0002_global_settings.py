"""global settings table

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-22
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Default LLM + SearXNG keys (plain, not secret)
DEFAULT_SETTINGS = [
    ("llm.base_url",           None,  False),
    ("llm.model",              None,  False),
    ("llm.api_key",            None,  True),   # secret
    ("llm.vision_base_url",    None,  False),
    ("llm.vision_model",       None,  False),
    ("llm.vision_api_key",     None,  True),   # secret
    ("llm.timeout_seconds",    "120", False),
    ("agent.interval_minutes", "10",  False),
    ("agent.auto_jira",        "true",False),
    ("agent.jira_severity_threshold", "critical", False),
    ("searxng.base_url",       None,  False),
    ("searxng.enabled",        "true",False),
    ("searxng.results_count",  "5",   False),
]


def upgrade() -> None:
    op.create_table(
        "global_settings",
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value_plain", sa.String(1024), nullable=True),
        sa.Column("value_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("key"),
    )
    op.bulk_insert(
        sa.table(
            "global_settings",
            sa.column("key", sa.String),
            sa.column("value_plain", sa.String),
            sa.column("is_secret", sa.Boolean),
        ),
        [{"key": k, "value_plain": v, "is_secret": s} for k, v, s in DEFAULT_SETTINGS],
    )


def downgrade() -> None:
    op.drop_table("global_settings")
