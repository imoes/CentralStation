"""Projektkarte: priority, assignee, labels, story_points, due_date, acceptance_criteria

Revision ID: 0036
Revises: 0035
Create Date: 2026-06-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("project_steps", sa.Column("priority", sa.String(20), nullable=False, server_default="medium"))
    op.add_column("project_steps", sa.Column("assignee", sa.String(256), nullable=True))
    op.add_column("project_steps", sa.Column("labels", sa.Text(), nullable=True))
    op.add_column("project_steps", sa.Column("story_points", sa.Integer(), nullable=True))
    op.add_column("project_steps", sa.Column("due_date", sa.Date(), nullable=True))
    op.add_column("project_steps", sa.Column("acceptance_criteria", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("project_steps", "acceptance_criteria")
    op.drop_column("project_steps", "due_date")
    op.drop_column("project_steps", "story_points")
    op.drop_column("project_steps", "labels")
    op.drop_column("project_steps", "assignee")
    op.drop_column("project_steps", "priority")
