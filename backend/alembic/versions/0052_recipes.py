"""recipes: bewährte Lese-Schritte aus gelösten Konsolen-Sitzungen, je Fehlermuster/Host

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0052"
down_revision: Union[str, None] = "0051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recipes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # template = LogTemplate.key (Log-Muster oder Flotten-Service), host = FQDN
        sa.Column("scope_type", sa.String(10), nullable=False),
        sa.Column("scope_key", sa.String(255), nullable=False, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("problem", sa.Text, nullable=True),
        sa.Column("solution", sa.Text, nullable=True),
        sa.Column("steps", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("other_commands", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("source_session_id", sa.String(64), nullable=True),
        sa.Column("source_incident_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("applied_count", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("recipes")
