"""store Jira connector identity on project steps and Kanban cards

Revision ID: 0044
Revises: 0043
Create Date: 2026-09-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add(table: str, index: str, fk: str) -> None:
    op.add_column(table, sa.Column("jira_connector_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(fk, table, "connector_configs", ["jira_connector_id"], ["id"], ondelete="SET NULL")
    op.create_index(index, table, ["jira_connector_id"])


def upgrade() -> None:
    _add("project_steps", "ix_project_steps_jira_connector_id", "fk_project_steps_jira_connector")
    _add("kanban_cards", "ix_kanban_cards_jira_connector_id", "fk_kanban_cards_jira_connector")


def downgrade() -> None:
    for table, index, fk in (
        ("kanban_cards", "ix_kanban_cards_jira_connector_id", "fk_kanban_cards_jira_connector"),
        ("project_steps", "ix_project_steps_jira_connector_id", "fk_project_steps_jira_connector"),
    ):
        op.drop_index(index, table_name=table)
        op.drop_constraint(fk, table, type_="foreignkey")
        op.drop_column(table, "jira_connector_id")
