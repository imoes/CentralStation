"""Step implementation_notes: code snippets and bash commands from planner

Revision ID: 0037
Revises: 0036
Create Date: 2026-06-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("project_steps", sa.Column("implementation_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("project_steps", "implementation_notes")
