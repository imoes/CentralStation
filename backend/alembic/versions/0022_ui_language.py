"""user_preferences.ui_language for per-user UI + AI language

Revision ID: 0022_ui_language
Revises: 0021_incidents
Create Date: 2026-06-04 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0022_ui_language"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_preferences",
        sa.Column("ui_language", sa.String(length=8), nullable=True, server_default="en"),
    )


def downgrade() -> None:
    op.drop_column("user_preferences", "ui_language")
