"""alert_feedback: Rückmeldungen zu Alarmen einzeln speichern (Labels für ein gelerntes Scoring); Nutzer 09.11.

Revision ID: 0064
Revises: 0063
Create Date: 2026-11-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0064"
down_revision: Union[str, None] = "0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ARTEN = ("quittiert", "ki_analyse_angefordert", "ausgeblendet", "ticket")


def upgrade() -> None:
    op.create_table(
        "alert_feedback",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("alert_id", sa.String(255), nullable=False, index=True),
        sa.Column("quelle", sa.String(50), nullable=False, server_default=""),
        sa.Column("muster", sa.String(12), nullable=False, index=True),
        sa.Column("art", sa.String(30), nullable=False, index=True),
        sa.Column("nutzer", UUID(as_uuid=True), nullable=True),
        sa.Column("alarm_zeit", sa.DateTime(timezone=True), nullable=True),
        sa.Column("zeitpunkt", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("details", JSONB, nullable=False, server_default="{}"),
        sa.CheckConstraint("art IN (" + ", ".join(f"'{a}'" for a in ARTEN) + ")", name="alert_feedback_art"),
    )


def downgrade() -> None:
    op.drop_table("alert_feedback")
