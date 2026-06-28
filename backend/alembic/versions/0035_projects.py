"""Projektbereich: projects, project_steps (mit Jira+Hierarchie), project_step_deps

Revision ID: 0035
Revises: 0034
Create Date: 2026-06-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="planning"),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "project_steps",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("parent_step_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("jira_issue_type", sa.String(30), nullable=False, server_default="task"),
        sa.Column("duration_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("est_start", sa.Integer(), nullable=True),
        sa.Column("est_end", sa.Integer(), nullable=True),
        sa.Column("lst_start", sa.Integer(), nullable=True),
        sa.Column("lst_end", sa.Integer(), nullable=True),
        sa.Column("slack", sa.Integer(), nullable=True),
        sa.Column("pos_x", sa.Integer(), nullable=True),
        sa.Column("pos_y", sa.Integer(), nullable=True),
        sa.Column("jira_connector_type", sa.String(30), nullable=True),
        sa.Column("jira_key", sa.String(50), nullable=True),
        sa.Column("jira_issue_id", sa.String(50), nullable=True),
        sa.Column("jira_status", sa.String(100), nullable=True),
        sa.Column("jira_status_category", sa.String(30), nullable=True),
        sa.Column("jira_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_step_id"], ["project_steps.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_steps_project_id", "project_steps", ["project_id"])
    op.create_index("ix_project_steps_jira_key", "project_steps", ["jira_key"])

    op.create_table(
        "project_step_deps",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("step_id", sa.UUID(), nullable=False),
        sa.Column("depends_on_step_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["step_id"], ["project_steps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["depends_on_step_id"], ["project_steps.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("step_id", "depends_on_step_id", name="uq_step_dep"),
    )


def downgrade() -> None:
    op.drop_table("project_step_deps")
    op.drop_index("ix_project_steps_jira_key", "project_steps")
    op.drop_index("ix_project_steps_project_id", "project_steps")
    op.drop_table("project_steps")
    op.drop_table("projects")
