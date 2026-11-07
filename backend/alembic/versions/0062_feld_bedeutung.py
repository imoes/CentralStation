"""feld_bedeutung: Bedeutung jedes Felds, jeder Hashtag-Art und jedes Signals einer Nachricht — die KI schlägt
nach (MCP lw_feld) und legt neue selbst an (lw_feld_anlegen); Nutzer 07.11.

Revision ID: 0062
Revises: 0061
Create Date: 2026-11-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0062"
down_revision: Union[str, None] = "0061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feld_bedeutung",
        sa.Column("name", sa.String(120), primary_key=True),       # feld:zeit · hashtag:regel · signal:neu
        sa.Column("art", sa.String(20), nullable=False, index=True),  # feld | hashtag | signal
        sa.Column("bedeutung", sa.Text, nullable=False),
        sa.Column("beispiel", sa.String(300), nullable=False, server_default=""),
        sa.Column("herkunft", sa.String(20), nullable=False),        # system | ki | mensch
        sa.Column("quelle", sa.Text, nullable=False, server_default=""),  # woher die Bedeutung stammt (URL, Code)
        sa.Column("angelegt_von", sa.String(200), nullable=False),
        sa.Column("angelegt_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("feld_bedeutung")
