"""user preferences, jira queries, work sessions

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-22
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── User Preferences ────────────────────────────────────────────────────
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("setup_completed", sa.Boolean(), nullable=False, server_default="false"),
        # ITIL preferences
        sa.Column("jira_project", sa.String(50), nullable=True),
        sa.Column("jira_default_assignee_filter", sa.String(20),
                  nullable=False, server_default="me"),  # me | all | team
        sa.Column("sla_notify_p1_minutes", sa.Integer(), server_default="15"),
        sa.Column("sla_notify_p2_minutes", sa.Integer(), server_default="60"),
        # Notification preferences (JSON: {channel: bool})
        sa.Column("notification_settings", sa.JSON(), nullable=True),
        # O365 mailbox for workflow
        sa.Column("o365_mailbox", sa.String(200), nullable=True),
        sa.Column("o365_folder", sa.String(100), nullable=True, server_default="Inbox"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("user_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    # ── Per-User JQL Queries ─────────────────────────────────────────────────
    op.create_table(
        "user_jira_queries",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("jql", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("show_in_widget", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    # ── Work Sessions (ITIL Incident/Request documentation) ─────────────────
    op.create_table(
        "work_sessions",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.UUID(), nullable=False),
        # Links
        sa.Column("jira_key", sa.String(50), nullable=True),
        sa.Column("jira_issue_id", sa.String(50), nullable=True),
        sa.Column("alert_id", sa.UUID(), nullable=True),
        # ITIL fields
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("subcategory", sa.String(100), nullable=True),
        sa.Column("impact", sa.String(20), nullable=True),      # high|medium|low
        sa.Column("urgency", sa.String(20), nullable=True),     # high|medium|low
        sa.Column("priority", sa.String(10), nullable=True),    # P1..P4
        sa.Column("status", sa.String(30), nullable=False, server_default="in_progress"),
        # acknowledged|in_progress|pending|resolved|closed
        sa.Column("closure_code", sa.String(50), nullable=True),
        # solved_permanently|solved_workaround|no_fault_found|duplicate|user_error|cancelled
        sa.Column("resolution_type", sa.String(30), nullable=True),
        # workaround|permanent_fix
        # Work notes (user + AI)
        sa.Column("work_notes", sa.JSON(), nullable=True),
        # list of {timestamp, author, type(user|ai), content}
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("resolution_summary", sa.Text(), nullable=True),
        sa.Column("ai_suggested_solution", sa.Text(), nullable=True),
        sa.Column("kedb_references", sa.JSON(), nullable=True),
        # list of matched known error references
        # Related O365 mails
        sa.Column("related_mail_ids", sa.JSON(), nullable=True),
        # SLA tracking
        sa.Column("sla_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], ondelete="SET NULL"),
    )

    # ── Default JQL queries for all existing users ────────────────────────
    # (new users get defaults via API; existing users get one default)
    # We skip bulk insert for existing users - handled in API on first access


def downgrade() -> None:
    op.drop_table("work_sessions")
    op.drop_table("user_jira_queries")
    op.drop_table("user_preferences")
