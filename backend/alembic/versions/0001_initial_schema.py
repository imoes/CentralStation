"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-22

"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "connector_configs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=True),
        sa.Column("encrypted_credentials", sa.LargeBinary(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "alerts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("location_name", sa.String(100), nullable=True),
        sa.Column("location_city", sa.String(100), nullable=True),
        sa.Column("acknowledged_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["acknowledged_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alerts_source", "alerts", ["source"])
    op.create_index("ix_alerts_severity", "alerts", ["severity"])
    op.create_index("ix_alerts_status", "alerts", ["status"])
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"])

    op.create_table(
        "kanban_cards",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="backlog"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("jira_key", sa.String(50), nullable=True),
        sa.Column("jira_issue_id", sa.String(50), nullable=True),
        sa.Column("assigned_to", sa.UUID(), nullable=True),
        sa.Column("alert_id", sa.UUID(), nullable=True),
        sa.Column("ai_generated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kanban_cards_status", "kanban_cards", ["status"])
    op.create_index("ix_kanban_cards_jira_key", "kanban_cards", ["jira_key"])

    op.create_table(
        "ai_analyses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("agent_type", sa.String(30), nullable=False, server_default="sysadmin"),
        sa.Column("run_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("sources_checked", sa.JSON(), nullable=True),
        sa.Column("findings", sa.JSON(), nullable=True),
        sa.Column("recommendations", sa.JSON(), nullable=True),
        sa.Column("severity_summary", sa.String(20), nullable=True),
        sa.Column("jira_tickets_created", sa.JSON(), nullable=True),
        sa.Column("rag_queries_used", sa.JSON(), nullable=True),
        sa.Column("token_usage", sa.JSON(), nullable=True),
        sa.Column("duration_seconds", sa.Numeric(8, 2), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_analyses_run_at", "ai_analyses", ["run_at"])

    op.create_table(
        "network_switch_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("switch_name", sa.String(50), nullable=False),
        sa.Column("switch_type", sa.String(10), nullable=True),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("location_name", sa.String(50), nullable=True),
        sa.Column("location_city", sa.String(100), nullable=True),
        sa.Column("vendor", sa.String(50), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False, server_default="info"),
        sa.Column("graylog_message_id", sa.String(255), nullable=True),
        sa.Column("dedup_key", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("acknowledged_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["acknowledged_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_nse_switch_name", "network_switch_events", ["switch_name"])
    op.create_index("ix_nse_status", "network_switch_events", ["status"])
    op.create_index("ix_nse_created_at", "network_switch_events", ["created_at"])
    op.create_index("ix_nse_dedup_key", "network_switch_events", ["dedup_key"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=True),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("old_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("network_switch_events")
    op.drop_table("ai_analyses")
    op.drop_table("kanban_cards")
    op.drop_table("alerts")
    op.drop_table("connector_configs")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
