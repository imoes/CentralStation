"""remote_exec_log: jede Lese-Vorlage, die auf einem Server lief (Prüf-Container)

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050"
down_revision: Union[str, None] = "0049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "remote_exec_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("source", sa.String(20), nullable=False),        # mcp | checks | recipe
        sa.Column("host", sa.String(255), nullable=False, index=True),
        sa.Column("template", sa.String(40), nullable=False),
        sa.Column("params", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("exit_code", sa.Integer, nullable=True),
        sa.Column("error", sa.String(40), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("output_bytes", sa.Integer, nullable=True),
        sa.Column("context", postgresql.JSONB, nullable=True),     # incident_id, session …
    )


def downgrade() -> None:
    op.drop_table("remote_exec_log")
