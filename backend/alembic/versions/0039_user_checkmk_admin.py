"""CheckMK-Admin permission: grants the full VibeMK tool set (incl. configuration)

Modelled as an orthogonal boolean, NOT as a `role` value: `role` is the job function
and holds exactly one value, so a `checkmk_admin` role would make it impossible to be
a sysadmin AND hold CheckMK configuration rights. Keeping the permission separate
also stops "role" from meaning two different things.

Revision ID: 0039
Revises: 0038
Create Date: 2026-08-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("checkmk_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "checkmk_admin")
