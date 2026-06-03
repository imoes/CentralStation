"""incidents + incident_members tables

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-03
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("primary_host", sa.String(255), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_incidents_primary_host", "incidents", ["primary_host"])
    op.create_index("ix_incidents_created_at", "incidents", ["created_at"])
    op.create_index("ix_incidents_status", "incidents", ["status"])

    op.create_table(
        "incident_members",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("incident_id", sa.UUID(), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("source", sa.String(40), nullable=False, server_default=""),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_incident_members_incident_id", "incident_members", ["incident_id"])
    op.create_index("ix_incident_members_external_id", "incident_members", ["external_id"])


def downgrade() -> None:
    op.drop_table("incident_members")
    op.drop_table("incidents")
