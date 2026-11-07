"""ki_frage: Rückfragen zwischen KI-Instanzen (und von der Konsole an die Analyse-Agenten) mit Kontext —
wie Peer-Sessions; Nutzer 07.11.

Revision ID: 0063
Revises: 0062
Create Date: 2026-11-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0063"
down_revision: Union[str, None] = "0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ki_frage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("von", sa.String(200), nullable=False),            # Agent-Name oder konsole:<email>
        sa.Column("an_host", sa.String(255), nullable=False, index=True),
        sa.Column("an_agent", sa.String(64), nullable=True, index=True),
        sa.Column("frage", sa.Text, nullable=False),
        sa.Column("kontext", JSONB, nullable=False, server_default="{}"),
        # offen | zugestellt | beantwortet | unbeantwortet | niemand
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("grund", sa.Text, nullable=False, server_default=""),
        sa.Column("antwort", sa.Text, nullable=True),
        sa.Column("belege", JSONB, nullable=False, server_default="[]"),
        sa.Column("gestellt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("beantwortet_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("ki_frage")
