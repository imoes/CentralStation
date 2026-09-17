"""store Jira connector identity on work sessions

Revision ID: 0043
Revises: 0042
Create Date: 2026-09-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("work_sessions", sa.Column("jira_connector_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_work_sessions_jira_connector", "work_sessions", "connector_configs",
        ["jira_connector_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_work_sessions_jira_connector_id", "work_sessions", ["jira_connector_id"])


def downgrade() -> None:
    op.drop_index("ix_work_sessions_jira_connector_id", table_name="work_sessions")
    op.drop_constraint("fk_work_sessions_jira_connector", "work_sessions", type_="foreignkey")
    op.drop_column("work_sessions", "jira_connector_id")
