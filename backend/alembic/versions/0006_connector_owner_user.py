"""add owner_user_id to connector configs

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "connector_configs",
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_connector_configs_owner_user_id",
        "connector_configs",
        ["owner_user_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_connector_configs_owner_user_id_users",
        "connector_configs",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_connector_configs_owner_user_id_users", "connector_configs", type_="foreignkey")
    op.drop_index("ix_connector_configs_owner_user_id", table_name="connector_configs")
    op.drop_column("connector_configs", "owner_user_id")
